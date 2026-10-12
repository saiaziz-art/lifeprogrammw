"""Gmail and Microsoft 365/Outlook via OAuth 2.0 (authorisation code + PKCE).

Tokens are encrypted at rest (Fernet, PLOT_SECRET_KEY). Scopes are the minimum needed:
- Google: gmail.compose (create drafts, send) + gmail.readonly (find replies in the thread)
- Microsoft: Mail.ReadWrite + Mail.Send + User.Read + offline_access

PLOT records what the provider actually returns (message id, thread/conversation id). An
accepted send is "accepted by provider", never "delivered".
"""

from __future__ import annotations

import base64
import datetime as dt
import email.utils
import hashlib
import json
import secrets
from email.message import EmailMessage
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from plot.config import get_settings
from plot.models import AuditLog, MailAccount, OAuthState, utcnow
from plot.security.crypto import decrypt, encrypt, get_secret

PROVIDERS = {
    "google": {
        "auth": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "scopes": ["https://www.googleapis.com/auth/gmail.compose", "https://www.googleapis.com/auth/gmail.readonly"],
        "client_id": "google_client_id",
        "client_secret": "google_client_secret",
    },
    "microsoft": {
        "auth": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "scopes": ["offline_access", "User.Read", "Mail.ReadWrite", "Mail.Send"],
        "client_id": "ms_client_id",
        "client_secret": "ms_client_secret",
    },
}
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
GRAPH = "https://graph.microsoft.com/v1.0/me"


class MailError(RuntimeError):
    def __init__(self, message: str, *, ambiguous: bool = False):
        super().__init__(message)
        self.ambiguous = ambiguous  # the provider may have accepted the message


def redirect_uri() -> str:
    return get_settings().public_url.rstrip("/") + "/api/mail/oauth/callback"


def _client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(timeout=30, transport=transport, headers={"User-Agent": "PLOT/1.0 (private use)"})


def start_oauth(db: Session, provider: str) -> str:
    if get_settings().is_sandbox:
        raise MailError("Mailbox connections are disabled in the sandbox environment")
    cfg = PROVIDERS.get(provider)
    if cfg is None:
        raise MailError("unknown provider")
    client_id = get_secret(db, cfg["client_id"])
    if not client_id:
        raise MailError(f"Set {cfg['client_id']} (and {cfg['client_secret']}) in Admin → Integrations first")
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(32)
    db.add(OAuthState(state=state, provider=provider, verifier_ciphertext=encrypt(verifier)))
    db.flush()
    params = {"client_id": client_id, "redirect_uri": redirect_uri(), "response_type": "code", "scope": " ".join(cfg["scopes"]), "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}
    if provider == "google":
        params |= {"access_type": "offline", "prompt": "consent", "include_granted_scopes": "true"}
    else:
        params |= {"response_mode": "query"}
    return f"{cfg['auth']}?{urlencode(params)}"


def finish_oauth(db: Session, state: str, code: str, actor: str, *, transport: httpx.BaseTransport | None = None) -> MailAccount:
    st = db.get(OAuthState, state)
    if st is None or utcnow() - st.created_at > dt.timedelta(minutes=15):
        raise MailError("OAuth state not recognised or expired — start the connection again")
    provider = st.provider
    cfg = PROVIDERS[provider]
    verifier = decrypt(st.verifier_ciphertext)
    db.delete(st)
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri(), "client_id": get_secret(db, cfg["client_id"]), "code_verifier": verifier}
    sec = get_secret(db, cfg["client_secret"])
    if sec:
        data["client_secret"] = sec
    if provider == "microsoft":
        data["scope"] = " ".join(cfg["scopes"])
    with _client(transport) as c:
        r = c.post(cfg["token"], data=data)
        if r.status_code >= 400:
            raise MailError(f"token exchange failed: HTTP {r.status_code} {r.text[:200]}")
        tok = r.json()
        tok["obtained_at"] = utcnow().timestamp()
        h = {"Authorization": f"Bearer {tok['access_token']}"}
        if provider == "google":
            me = c.get(f"{GMAIL}/profile", headers=h).json()
            address = me.get("emailAddress")
        else:
            me = c.get(GRAPH, headers=h).json()
            address = me.get("mail") or me.get("userPrincipalName")
    if not address:
        raise MailError("could not read the mailbox address")
    acct = db.scalar(select(MailAccount).where(MailAccount.provider == provider, MailAccount.email_address == address))
    if acct is None:
        acct = MailAccount(provider=provider, email_address=address, token_ciphertext=b"")
        db.add(acct)
    acct.token_ciphertext = encrypt(json.dumps(tok))
    acct.scopes = tok.get("scope") or " ".join(cfg["scopes"])
    acct.status = "connected"
    acct.last_error = None
    db.add(AuditLog(actor=actor, action="mail.connected", entity="mail_account", entity_id=address, detail={"provider": provider, "scopes": acct.scopes}))
    db.flush()
    return acct


def _token(db: Session, acct: MailAccount, c: httpx.Client) -> str:
    tok = json.loads(decrypt(acct.token_ciphertext))
    if tok.get("obtained_at", 0) + int(tok.get("expires_in", 3600)) - 120 > utcnow().timestamp():
        return tok["access_token"]
    if not tok.get("refresh_token"):
        acct.status = "reauth_required"
        raise MailError("mailbox authorisation expired — reconnect it in Admin → Mail")
    cfg = PROVIDERS[acct.provider]
    data = {"grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": get_secret(db, cfg["client_id"])}
    sec = get_secret(db, cfg["client_secret"])
    if sec:
        data["client_secret"] = sec
    if acct.provider == "microsoft":
        data["scope"] = " ".join(cfg["scopes"])
    r = c.post(cfg["token"], data=data)
    if r.status_code >= 400:
        acct.status = "reauth_required"
        acct.last_error = f"refresh failed HTTP {r.status_code}"
        raise MailError("mailbox authorisation could not be refreshed — reconnect it in Admin → Mail")
    new = r.json()
    tok.update({k: v for k, v in new.items() if v})
    tok["obtained_at"] = utcnow().timestamp()
    acct.token_ciphertext = encrypt(json.dumps(tok))
    return tok["access_token"]


def build_mime(*, from_name: str, from_addr: str, to_name: str, to_addr: str, subject: str, text: str, message_id: str, reference: str, in_reply_to: str | None = None) -> EmailMessage:
    m = EmailMessage()
    m["From"] = email.utils.formataddr((from_name, from_addr))
    m["To"] = email.utils.formataddr((to_name, to_addr))
    m["Subject"] = subject
    m["Date"] = email.utils.formatdate(localtime=False)
    m["Message-ID"] = message_id
    m["X-PLOT-Reference"] = reference
    if in_reply_to:
        m["In-Reply-To"] = in_reply_to
        m["References"] = in_reply_to
    m.set_content(text)
    return m


class Mailbox:
    """Thin provider client; every method returns what the provider actually said."""

    def __init__(self, db: Session, acct: MailAccount, transport: httpx.BaseTransport | None = None):
        self.db, self.acct = db, acct
        self.c = _client(transport)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.c.close()

    def _h(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {_token(self.db, self.acct, self.c)}"}

    def create_draft(self, mime: EmailMessage, thread_id: str | None = None) -> dict[str, Any]:
        if self.acct.provider == "google":
            raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
            body: dict[str, Any] = {"message": {"raw": raw}}
            if thread_id:
                body["message"]["threadId"] = thread_id
            r = self.c.post(f"{GMAIL}/drafts", headers=self._h(), json=body)
            r.raise_for_status()
            d = r.json()
            return {"draft_id": d["id"], "thread_id": d.get("message", {}).get("threadId")}
        r = self.c.post(f"{GRAPH}/messages", headers=self._h(), json=_graph_message(mime))
        r.raise_for_status()
        d = r.json()
        return {"draft_id": d["id"], "thread_id": d.get("conversationId"), "internet_message_id": d.get("internetMessageId")}

    def send(self, mime: EmailMessage, thread_id: str | None = None) -> dict[str, Any]:
        """Send exactly this MIME message. Raises MailError(ambiguous=True) if the outcome is unknown."""
        try:
            if self.acct.provider == "google":
                raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
                body: dict[str, Any] = {"raw": raw}
                if thread_id:
                    body["threadId"] = thread_id
                r = self.c.post(f"{GMAIL}/messages/send", headers=self._h(), json=body)
                if r.status_code >= 500:
                    raise MailError(f"Gmail returned HTTP {r.status_code}", ambiguous=True)
                if r.status_code >= 400:
                    raise MailError(f"Gmail refused the message: HTTP {r.status_code} {r.text[:200]}")
                d = r.json()
                return {"message_id": d["id"], "thread_id": d.get("threadId"), "labels": d.get("labelIds", []), "internet_message_id": mime["Message-ID"]}
            d = self.c.post(f"{GRAPH}/messages", headers=self._h(), json=_graph_message(mime))
            if d.status_code >= 400:
                raise MailError(f"Outlook refused the draft: HTTP {d.status_code} {d.text[:200]}")
            created = d.json()
            r = self.c.post(f"{GRAPH}/messages/{created['id']}/send", headers=self._h())
            if r.status_code >= 500:
                raise MailError(f"Outlook returned HTTP {r.status_code}", ambiguous=True)
            if r.status_code >= 400:
                raise MailError(f"Outlook refused to send: HTTP {r.status_code} {r.text[:200]}")
            return {"message_id": created.get("internetMessageId") or created["id"], "thread_id": created.get("conversationId"), "graph_id": created["id"], "internet_message_id": created.get("internetMessageId")}
        except httpx.TimeoutException as exc:
            raise MailError(f"timed out waiting for the provider: {exc}", ambiguous=True) from exc
        except httpx.TransportError as exc:
            raise MailError(f"network error talking to the provider: {exc}", ambiguous=True) from exc

    def find_by_message_id(self, internet_message_id: str) -> dict[str, Any] | None:
        """Reconcile an ambiguous send: is the message in the mailbox?"""
        if self.acct.provider == "google":
            r = self.c.get(f"{GMAIL}/messages", headers=self._h(), params={"q": f"rfc822msgid:{internet_message_id}", "includeSpamTrash": "false"})
            r.raise_for_status()
            msgs = r.json().get("messages") or []
            return {"message_id": msgs[0]["id"], "thread_id": msgs[0].get("threadId")} if msgs else None
        r = self.c.get(f"{GRAPH}/messages", headers=self._h(), params={"$filter": f"internetMessageId eq '{internet_message_id}'", "$select": "id,conversationId,internetMessageId"})
        r.raise_for_status()
        v = r.json().get("value") or []
        return {"message_id": v[0].get("internetMessageId") or v[0]["id"], "thread_id": v[0].get("conversationId")} if v else None

    def thread_messages(self, thread_id: str) -> list[dict[str, Any]]:
        out = []
        if self.acct.provider == "google":
            r = self.c.get(f"{GMAIL}/threads/{thread_id}", headers=self._h(), params={"format": "full"})
            r.raise_for_status()
            for m in r.json().get("messages", []):
                hdr = {h["name"].lower(): h["value"] for h in m.get("payload", {}).get("headers", [])}
                out.append({"id": m["id"], "from": hdr.get("from", ""), "subject": hdr.get("subject"), "date": dt.datetime.fromtimestamp(int(m.get("internalDate", 0)) / 1000, dt.UTC), "body": _gmail_text(m.get("payload", {})), "labels": m.get("labelIds", [])})
            return out
        r = self.c.get(f"{GRAPH}/messages", headers=self._h(), params={"$filter": f"conversationId eq '{thread_id}'", "$select": "id,subject,from,receivedDateTime,body,internetMessageId"})
        r.raise_for_status()
        for m in r.json().get("value", []):
            fr = (m.get("from") or {}).get("emailAddress") or {}
            body = (m.get("body") or {}).get("content", "")
            if (m.get("body") or {}).get("contentType", "").lower() == "html":
                body = _strip_html(body)
            out.append({"id": m.get("internetMessageId") or m["id"], "from": email.utils.formataddr((fr.get("name", ""), fr.get("address", ""))), "subject": m.get("subject"), "date": dt.datetime.fromisoformat(m["receivedDateTime"].replace("Z", "+00:00")), "body": body, "labels": []})
        return out


def _graph_message(mime: EmailMessage) -> dict[str, Any]:
    name, addr = email.utils.parseaddr(mime["To"])
    return {
        "subject": mime["Subject"],
        "body": {"contentType": "Text", "content": mime.get_content()},
        "toRecipients": [{"emailAddress": {"address": addr, "name": name}}],
        "internetMessageHeaders": [{"name": "X-PLOT-Reference", "value": mime["X-PLOT-Reference"]}],
    }


def _gmail_text(payload: dict[str, Any]) -> str:
    if payload.get("mimeType", "").startswith("text/plain") and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"] + "==").decode("utf-8", "replace")
    for p in payload.get("parts", []) or []:
        t = _gmail_text(p)
        if t:
            return t
    if payload.get("mimeType", "").startswith("text/html") and payload.get("body", {}).get("data"):
        return _strip_html(base64.urlsafe_b64decode(payload["body"]["data"] + "==").decode("utf-8", "replace"))
    return ""


def _strip_html(s: str) -> str:
    import re

    s = re.sub(r"(?is)<(script|style).*?</\1>", "", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>", "\n", s)
    return re.sub(r"<[^>]+>", "", s).strip()


def strip_quoted(text: str) -> str:
    """Keep the owner's new words, not the quoted original."""
    import re

    lines = []
    for ln in text.splitlines():
        if ln.startswith(">") or re.match(r"^On .+ wrote:\s*$", ln) or re.match(r"^-{2,}\s*Original Message", ln, re.I) or re.match(r"^From: ", ln):
            break
        lines.append(ln)
    return "\n".join(lines).strip()


def connected_account(db: Session) -> MailAccount | None:
    return db.scalar(select(MailAccount).where(MailAccount.status == "connected").order_by(MailAccount.connected_at.desc()).limit(1))
