from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from plot.db import get_db
from plot.models import AuditLog, User
from plot.security.auth import SESSION_COOKIE, resolve_session


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = resolve_session(db, request.cookies.get(SESSION_COOKIE))
    if user is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    db.commit()
    return user


def audit(db: Session, user: User | str, action: str, entity: str | None = None, entity_id: str | int | None = None, **detail) -> None:
    db.add(AuditLog(actor=user if isinstance(user, str) else user.email, action=action, entity=entity, entity_id=str(entity_id) if entity_id is not None else None, detail=detail))
