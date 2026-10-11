"""FastAPI application. All routes under /api; every route except login/setup/health
requires a session. Unsafe methods require the X-Requested-With: plot header (CSRF defence
in addition to SameSite=Lax cookies)."""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from plot import __version__
from plot.api import routes_admin, routes_sites, routes_workflow
from plot.api.deps import audit, current_user
from plot.config import get_settings
from plot.db import get_db
from plot.models import User
from plot.security.auth import MIN_PASSWORD_LENGTH, SESSION_COOKIE, authenticate, create_session, create_user, revoke_session

app = FastAPI(title="PLOT API", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)

_login_attempts: dict[str, deque] = defaultdict(deque)


@app.middleware("http")
async def csrf_guard(request: Request, call_next):
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.url.path.startswith("/api/"):
        if request.headers.get("x-requested-with") != "plot":
            return JSONResponse({"detail": "missing X-Requested-With header"}, status_code=403)
    resp: Response = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Cache-Control", "no-store")
    return resp


class LoginIn(BaseModel):
    email: str
    password: str


@app.get("/api/health")
def health(db: Session = Depends(get_db)) -> dict:
    db.execute(text("select 1"))
    return {"ok": True, "version": __version__}


@app.get("/api/auth/state")
def auth_state(request: Request, db: Session = Depends(get_db)) -> dict:
    from plot.security.auth import resolve_session

    n = db.scalar(select(func.count()).select_from(User))
    user = resolve_session(db, request.cookies.get(SESSION_COOKIE))
    db.commit()
    return {"setup_required": n == 0, "authenticated": user is not None, "email": user.email if user else None}


def _set_cookie(resp: Response, token: str) -> None:
    s = get_settings()
    resp.set_cookie(SESSION_COOKIE, token, httponly=True, secure=s.secure_cookies, samesite="lax", max_age=s.session_days * 86400, path="/")


@app.post("/api/auth/setup")
def setup(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    if db.scalar(select(func.count()).select_from(User)):
        raise HTTPException(409, "already set up — log in instead")
    if len(body.password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(422, f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    user = create_user(db, body.email, body.password)
    token = create_session(db, user, request.headers.get("user-agent"))
    audit(db, user, "auth.setup")
    db.commit()
    _set_cookie(response, token)
    return {"ok": True, "email": user.email}


@app.post("/api/auth/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    ip = request.client.host if request.client else "?"
    q = _login_attempts[ip]
    now = time.monotonic()
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= 8:
        raise HTTPException(429, "too many attempts; wait a minute")
    q.append(now)
    user = authenticate(db, body.email, body.password)
    if user is None:
        audit(db, body.email[:320], "auth.login_failed", ip=ip)
        db.commit()
        raise HTTPException(401, "invalid email or password")
    token = create_session(db, user, request.headers.get("user-agent"))
    audit(db, user, "auth.login", ip=ip)
    db.commit()
    _set_cookie(response, token)
    return {"ok": True, "email": user.email}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    revoke_session(db, request.cookies.get(SESSION_COOKIE))
    db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@app.get("/api/auth/me")
def me(user: User = Depends(current_user)) -> dict:
    return {"email": user.email}


app.include_router(routes_sites.router, prefix="/api", dependencies=[Depends(current_user)])
app.include_router(routes_workflow.router, prefix="/api", dependencies=[Depends(current_user)])
app.include_router(routes_admin.router, prefix="/api", dependencies=[Depends(current_user)])
