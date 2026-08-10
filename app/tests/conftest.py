"""Pytest configuration.

Runs Alembic migrations against a disposable SQLite database so the suite
exercises the real migration chain (no `create_all` anywhere).
"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEST_DB = ROOT / "test_customer_care.db"

# Must be set before any app module is imported.
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"
os.environ["JWT_SECRET_KEY"] = "test-secret-key-not-for-production"
os.environ["EXPOSE_OTP_IN_RESPONSE"] = "true"
os.environ["LOG_LEVEL"] = "WARNING"

import pytest  # noqa: E402
from alembic.config import Config as AlembicConfig  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import event, text  # noqa: E402

from alembic import command  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.db.session import SessionLocal, engine, get_db  # noqa: E402
from app.main import create_app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _prepare_database():
    """Create a fresh DB and apply all Alembic migrations once per session."""
    _remove_test_db_files()

    alembic_cfg = AlembicConfig(ROOT / "alembic.ini")
    command.upgrade(alembic_cfg, "head")

    # WAL journaling lets concurrent writers serialize instead of deadlocking
    # on shared read locks (needed by the refresh-rotation race test).
    with engine.connect() as connection:
        connection.execute(text("PRAGMA journal_mode=WAL"))

    yield

    engine.dispose()
    _remove_test_db_files()


@event.listens_for(engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
    """Enforce FK constraints on every pooled connection.

    SQLite's ``foreign_keys`` pragma is per-connection (and a no-op inside a
    transaction), so it must be set when each connection is created — this is
    what makes ON DELETE CASCADE / SET NULL behave during cleanup and tests.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def _remove_test_db_files() -> None:
    """Best-effort removal of the test DB and its WAL sidecar files."""
    for path in TEST_DB.parent.glob(f"{TEST_DB.name}*"):
        try:
            path.unlink()
        except OSError:
            pass


@pytest.fixture()
def db_session():
    """A bare session for direct DB manipulation inside tests."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _clean_tables(db_session):
    """Reset all tables between tests (children first, then parents)."""
    yield
    db_session.execute(text("DELETE FROM feedback"))
    db_session.execute(text("DELETE FROM ticket_comments"))
    db_session.execute(text("DELETE FROM tickets"))
    db_session.execute(text("DELETE FROM order_items"))
    db_session.execute(text("DELETE FROM orders"))
    db_session.execute(text("DELETE FROM chat_messages"))
    db_session.execute(text("DELETE FROM chat_conversations"))
    db_session.execute(text("DELETE FROM notifications"))
    db_session.execute(text("DELETE FROM addresses"))
    db_session.execute(text("DELETE FROM customer_profiles"))
    db_session.execute(text("DELETE FROM refresh_tokens"))
    db_session.execute(text("DELETE FROM knowledge_articles"))
    db_session.execute(text("DELETE FROM products"))
    db_session.execute(text("DELETE FROM users"))
    db_session.commit()


@pytest.fixture()
def client():
    """TestClient with the real app but an overridden DB dependency."""
    application = create_app()

    def _override_get_db():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    application.dependency_overrides[get_db] = _override_get_db
    with TestClient(application) as test_client:
        yield test_client
    application.dependency_overrides.clear()


def pytest_configure(config: pytest.Config) -> None:
    """Fail loudly if the wrong database is configured."""
    settings = get_settings()
    if not settings.DATABASE_URL.startswith("sqlite"):
        raise RuntimeError("Tests must run against SQLite. Refusing to proceed.")
