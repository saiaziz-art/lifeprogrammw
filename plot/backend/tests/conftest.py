"""Test configuration: a dedicated PostgreSQL/PostGIS database (PLOT_TEST_DATABASE_URL),
migrated with Alembic once per session and truncated between tests.

HTTP fixtures in tests/fixtures are TEST DATA shaped like the documented API responses;
they are never loaded into a live database. The one exception is
brownfield_new_forest_excerpt.csv: a verbatim excerpt of New Forest District Council's
published register (OGL v3.0) used to test parsing of real-world files.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

TEST_DB = os.environ.get("PLOT_TEST_DATABASE_URL", "postgresql+psycopg://postgres@127.0.0.1:5432/plot_test")
os.environ["PLOT_DATABASE_URL"] = TEST_DB
os.environ["PLOT_ENV"] = "test"
os.environ.setdefault("PLOT_SECRET_KEY", "Zm9yLXRlc3RzLW9ubHktMzItYnl0ZXMtbG9uZy1rZXk=")
os.environ["PLOT_HTTP_MAX_RETRIES"] = "0"

import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402

from plot.db import get_engine, new_session  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="session", autouse=True)
def _migrate():
    eng = get_engine()
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        c.execute(text("CREATE SCHEMA public"))
        c.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
    subprocess.check_call([sys.executable, "-m", "alembic", "-c", str(ROOT / "alembic.ini"), "upgrade", "head"], cwd=ROOT, env=os.environ.copy())
    yield


TABLES = [
    "inbound_messages", "outreach", "contacts", "title_documents", "mail_accounts", "oauth_states", "lead_events", "leads", "pipeline_items", "feedback", "overrides", "assessments", "listing_events", "listings",
    "evidence", "ingest_inbox", "processed_messages", "expenses", "sites", "source_runs", "digests", "runs", "jobs", "schedules",
    "ref_features", "api_cache", "audit_log", "user_sessions", "users", "secrets", "preferences_history", "preferences", "cost_rates",
    "service_heartbeats", "suppressions", "sources",
]


@pytest.fixture()
def db():
    eng = get_engine()
    with eng.begin() as c:
        c.execute(text("TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY CASCADE"))
    s = new_session()
    from plot.sources.registry import sync_sources

    sync_sources(s)
    s.commit()
    yield s
    s.close()


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")
