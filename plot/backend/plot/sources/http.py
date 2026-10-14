"""Polite, observable HTTP for public data APIs.

- identifies itself with a User-Agent
- retries only what is worth retrying (timeouts, 429, 5xx, transient connect errors)
- never retries policy denials (proxy 403) or 4xx
- classifies failures into SourceError classes shown verbatim in the admin screen
- optional response cache in Postgres (api_cache) to avoid hammering services
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import time
from typing import Any

import httpx
from sqlalchemy.orm import Session

from plot import __version__
from plot.config import get_settings
from plot.models import ApiCache, utcnow
from plot.sources.base import SourceError

log = logging.getLogger(__name__)


def _user_agent() -> str:
    return f"PLOT/{__version__} (private land research; {get_settings().http_contact})"


def classify(exc: Exception) -> SourceError:
    if isinstance(exc, SourceError):
        return exc
    if isinstance(exc, httpx.ProxyError):
        return SourceError(
            "network_blocked",
            f"outbound connection refused by the network egress proxy ({exc}); "
            "the host must be allowed in the deployment's network policy",
            http_status=403 if "403" in str(exc) else None,
        )
    if isinstance(exc, httpx.ConnectError):
        return SourceError("network_unreachable", f"could not connect: {exc}", retryable=True)
    if isinstance(exc, httpx.TimeoutException):
        return SourceError("timeout", f"request timed out: {exc}", retryable=True)
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 429:
            return SourceError("rate_limited", "rate limited by the service (HTTP 429)", http_status=code, retryable=True)
        if code >= 500:
            return SourceError("server_error", f"service error HTTP {code}", http_status=code, retryable=True)
        if code in (401, 403):
            return SourceError("forbidden", f"service refused access HTTP {code}", http_status=code)
        if code == 404:
            return SourceError("not_found", f"endpoint not found HTTP 404 ({exc.request.url})", http_status=code)
        return SourceError("bad_request", f"HTTP {code}: {exc.response.text[:300]}", http_status=code)
    if isinstance(exc, (json.JSONDecodeError, ValueError)):
        return SourceError("parse_error", f"unexpected response format: {exc}")
    return SourceError("unexpected", f"{type(exc).__name__}: {exc}")


class PublicHttp:
    def __init__(
        self,
        db: Session | None = None,
        *,
        timeout: float | None = None,
        max_retries: int | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep=time.sleep,
        auth: Any = None,
    ):
        s = get_settings()
        self.db = db
        self.max_retries = s.http_max_retries if max_retries is None else max_retries
        self._sleep = sleep
        self.client = httpx.Client(
            timeout=timeout or s.http_timeout_seconds,
            headers={"User-Agent": _user_agent(), "Accept": "application/json"},
            follow_redirects=True,
            transport=transport,
            auth=auth,
        )
        self.requests_made = 0

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> PublicHttp:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------ core

    def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        attempt = 0
        while True:
            attempt += 1
            try:
                self.requests_made += 1
                resp = self.client.request(method, url, **kwargs)
                resp.raise_for_status()
                return resp
            except Exception as exc:  # noqa: BLE001 - classified below
                err = classify(exc)
                if not err.retryable or attempt > self.max_retries:
                    raise err from exc
                delay = min(2 ** attempt, 30)
                if isinstance(exc, httpx.HTTPStatusError):
                    ra = exc.response.headers.get("Retry-After")
                    if ra and ra.isdigit():
                        delay = min(int(ra), 120)
                log.warning("retrying %s %s after %s (attempt %s): %s", method, url, delay, attempt, err)
                self._sleep(delay)

    def get_json(self, url: str, params: Any = None, *, cache_ttl: dt.timedelta | None = None, **kwargs) -> Any:
        key = None
        if cache_ttl and self.db is not None:
            req = self.client.build_request("GET", url, params=params)
            key = hashlib.sha256(str(req.url).encode()).hexdigest()
            row = self.db.get(ApiCache, key)
            if row is not None and row.fetched_at > utcnow() - cache_ttl:
                return row.body["json"]
        resp = self.request("GET", url, params=params, **kwargs)
        try:
            data = resp.json()
        except ValueError as exc:
            raise SourceError("parse_error", f"response from {resp.url} was not JSON: {resp.text[:200]}") from exc
        if key is not None:
            row = self.db.get(ApiCache, key)
            if row is None:
                self.db.add(ApiCache(key=key, url=str(resp.url), status=resp.status_code, body={"json": data}))
            else:
                row.body = {"json": data}
                row.fetched_at = utcnow()
                row.status = resp.status_code
        return data

    def get_text(self, url: str, params: Any = None, **kwargs) -> str:
        return self.request("GET", url, params=params, **kwargs).text

    def post_form_json(self, url: str, data: dict[str, Any], **kwargs) -> Any:
        resp = self.request("POST", url, data=data, **kwargs)
        try:
            return resp.json()
        except ValueError as exc:
            raise SourceError("parse_error", f"response from {resp.url} was not JSON") from exc
