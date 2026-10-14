"""Encryption of stored integration credentials (Fernet: AES-128-CBC + HMAC-SHA256).

The master key comes from PLOT_SECRET_KEY and never touches the database. If the key is
missing, credential storage is refused rather than silently storing plaintext.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from plot.config import get_settings
from plot.models import Secret


class SecretsUnavailable(RuntimeError):
    pass


def generate_key() -> str:
    return Fernet.generate_key().decode()


def _fernet() -> Fernet:
    key = get_settings().secret_key
    if not key:
        raise SecretsUnavailable("PLOT_SECRET_KEY is not set; encrypted credential storage is disabled")
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:  # malformed key
        raise SecretsUnavailable("PLOT_SECRET_KEY is not a valid Fernet key") from exc


def encrypt(plaintext: str) -> bytes:
    return _fernet().encrypt(plaintext.encode())


def decrypt(ciphertext: bytes) -> str:
    try:
        return _fernet().decrypt(ciphertext).decode()
    except InvalidToken as exc:
        raise SecretsUnavailable("stored credential cannot be decrypted with the current PLOT_SECRET_KEY") from exc


def set_secret(db: Session, key: str, value: str, actor: str) -> None:
    row = db.get(Secret, key)
    if row is None:
        row = Secret(key=key, ciphertext=encrypt(value), updated_by=actor)
        db.add(row)
    else:
        row.ciphertext = encrypt(value)
        row.updated_by = actor


def get_secret(db: Session, key: str) -> str | None:
    row = db.get(Secret, key)
    if row is None:
        return None
    try:
        return decrypt(row.ciphertext)
    except SecretsUnavailable:
        return None


def delete_secret(db: Session, key: str) -> None:
    row = db.get(Secret, key)
    if row is not None:
        db.delete(row)


def secret_status(db: Session, key: str) -> str:
    """'set' | 'missing' | 'undecryptable' — never returns the value."""
    row = db.get(Secret, key)
    if row is None:
        return "missing"
    try:
        decrypt(row.ciphertext)
        return "set"
    except SecretsUnavailable:
        return "undecryptable"
