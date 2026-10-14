"""Process liveness rows written by the scheduler and workers."""

from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from plot.models import ServiceHeartbeat, utcnow


def beat(db: Session, name: str, **detail) -> None:
    now = utcnow()
    stmt = insert(ServiceHeartbeat).values(name=name, at=now, detail=detail).on_conflict_do_update(index_elements=["name"], set_={"at": now, "detail": detail})
    db.execute(stmt)
