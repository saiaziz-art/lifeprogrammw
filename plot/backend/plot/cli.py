"""PLOT command line: `plot <command>`."""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import subprocess
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="plot")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("keygen", help="print a new PLOT_SECRET_KEY")
    sub.add_parser("migrate", help="apply database migrations")
    sub.add_parser("seed", help="register sources, default cost rates and schedules")
    cu = sub.add_parser("create-user", help="create a login")
    cu.add_argument("email")
    rn = sub.add_parser("run-now", help="run the sourcing workflow synchronously")
    rn.add_argument("--max-enrich", type=int, default=150)
    sub.add_parser("enqueue-run", help="queue a sourcing run for the worker")
    sub.add_parser("tick", help="run one scheduler tick (enqueue due jobs)")
    sub.add_parser("scheduler", help="run the scheduler process")
    sub.add_parser("worker", help="run a worker process")
    w1 = sub.add_parser("work-once", help="execute queued jobs then exit")
    w1.add_argument("--max", type=int, default=10)
    api = sub.add_parser("api", help="run the HTTP API")
    api.add_argument("--host", default="127.0.0.1")
    api.add_argument("--port", type=int, default=8000)
    sub.add_parser("status", help="print last runs and source health")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    if args.cmd == "keygen":
        from plot.security.crypto import generate_key

        print(generate_key())
        return 0
    if args.cmd == "migrate":
        here = Path(__file__).resolve().parent.parent
        return subprocess.call([sys.executable, "-m", "alembic", "-c", str(here / "alembic.ini"), "upgrade", "head"], cwd=here)
    if args.cmd == "seed":
        from plot.db import session_scope
        from plot.engine.costs.rates import seed_rates
        from plot.jobs.scheduler import ensure_default_schedules
        from plot.preferences import load_preferences
        from plot.sources.registry import sync_sources

        with session_scope() as db:
            sync_sources(db)
            n = seed_rates(db)
            ensure_default_schedules(db)
            load_preferences(db)
        print(f"seeded: sources synced, {n} cost rates added, default schedules ensured")
        return 0
    if args.cmd == "create-user":
        from plot.db import session_scope
        from plot.security.auth import create_user

        pw = getpass.getpass("password (min 12 chars): ")
        with session_scope() as db:
            create_user(db, args.email, pw)
        print("user created")
        return 0
    if args.cmd == "run-now":
        from plot.db import new_session
        from plot.jobs.pipeline import run_sourcing

        db = new_session()
        run = run_sourcing(db, trigger="cli", max_enrich=args.max_enrich)
        print(json.dumps({"run_id": run.id, "status": run.status, "summary": run.summary}, indent=2, default=str))
        return 0 if run.status in ("succeeded", "partial") else 1
    if args.cmd == "enqueue-run":
        from plot.db import session_scope
        from plot.jobs.queue import enqueue

        with session_scope() as db:
            j = enqueue(db, "daily_sourcing", {"trigger": "manual"})
            print(f"queued job {j.id}")
        return 0
    if args.cmd == "tick":
        from plot.db import session_scope
        from plot.jobs.scheduler import ensure_default_schedules, tick

        with session_scope() as db:
            ensure_default_schedules(db)
            print(f"enqueued: {tick(db)}")
        return 0
    if args.cmd == "scheduler":
        from plot.jobs.scheduler import run_forever

        run_forever()
        return 0
    if args.cmd == "worker":
        from plot.jobs.worker import run_forever

        run_forever()
        return 0
    if args.cmd == "work-once":
        import os
        import socket

        from plot.jobs.worker import run_one

        n = 0
        while n < args.max and run_one(f"{socket.gethostname()}:{os.getpid()}:once"):
            n += 1
        print(f"executed {n} job(s)")
        return 0
    if args.cmd == "api":
        import uvicorn

        uvicorn.run("plot.api.main:app", host=args.host, port=args.port)
        return 0
    if args.cmd == "status":
        from sqlalchemy import select

        from plot.db import session_scope
        from plot.models import Run, Source

        with session_scope() as db:
            for r in db.scalars(select(Run).order_by(Run.id.desc()).limit(5)).all():
                print(f"run #{r.id} {r.kind} {r.trigger} {r.status} {r.started_at:%Y-%m-%d %H:%M} → {r.finished_at and r.finished_at.strftime('%H:%M')} {r.summary}")
            for s in db.scalars(select(Source).order_by(Source.id)).all():
                print(f"  {s.id:34} {s.kind:9} {str(s.last_status):15} {(s.last_error or '')[:90]}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
