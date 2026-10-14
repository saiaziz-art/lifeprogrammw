"""Password hashing (argon2id) and server-side sessions (random token, SHA-256 stored)."""

from __future__ import annotations

import datetime as dt
import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from plot.config import get_settings
from plot.models import User, UserSession, utcnow

_hasher = PasswordHasher()

SESSION_COOKIE = "plot_session"
MIN_PASSWORD_LENGTH = 12


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def authenticate(db: Session, email: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None:
        # equalise timing against user enumeration
        _hasher.hash("timing-equaliser-password")
        return None
    if not verify_password(user.password_hash, password):
        return None
    if _hasher.check_needs_rehash(user.password_hash):
        user.password_hash = _hasher.hash(password)
    user.last_login_at = utcnow()
    return user


def create_session(db: Session, user: User, user_agent: str | None) -> str:
    token = secrets.token_urlsafe(32)
    db.add(
        UserSession(
            user_id=user.id,
            token_hash=_token_hash(token),
            expires_at=utcnow() + dt.timedelta(days=get_settings().session_days),
            user_agent=(user_agent or "")[:400],
        )
    )
    return token


def resolve_session(db: Session, token: str | None) -> User | None:
    if not token:
        return None
    sess = db.scalar(select(UserSession).where(UserSession.token_hash == _token_hash(token)))
    if sess is None or sess.expires_at < utcnow():
        return None
    sess.last_seen_at = utcnow()
    return sess.user


def revoke_session(db: Session, token: str | None) -> None:
    if token:
        db.execute(delete(UserSession).where(UserSession.token_hash == _token_hash(token)))


def create_user(db: Session, email: str, password: str, is_admin: bool = True) -> User:
    user = User(email=email.strip().lower(), password_hash=hash_password(password), is_admin=is_admin)
    db.add(user)
    db.flush()
    return user
