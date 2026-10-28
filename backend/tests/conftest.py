"""Shared pytest fixtures.

Tests run against the real local Postgres and Mongo containers, not mocks. The
`seeded` fixture builds a small, deterministic dataset once per session and each
test works inside a transaction that is rolled back.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import text

os.environ.setdefault("ENVIRONMENT", "local")

from app.db.engine import rls_session, service_session  # noqa: E402
from app.security import auth  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session", autouse=True)
def _ensure_schema() -> None:
    """Apply migrations once so a fresh clone can run pytest directly."""
    from app.db.migrate import run

    run()


@pytest.fixture
def db_session() -> Iterator:
    with service_session() as session:
        yield session


@pytest.fixture
def claims_factory():
    """Build a JWT claim set for a role, exactly as the API would."""

    def _make(role: str, **app_metadata) -> dict:
        return {
            "sub": str(uuid.uuid4()),
            "role": "authenticated",
            "email": f"{role}@cleanslate.test",
            "app_metadata": {"role": role, **app_metadata},
        }

    return _make


@pytest.fixture
def token_factory():
    def _make(role: str, **app_metadata) -> str:
        return auth.create_access_token(
            user_id=uuid.uuid4(),
            email=f"{role}@cleanslate.test",
            role=role,
            **{k: v for k, v in app_metadata.items() if k in {"supplier_id", "supplier_code"}},
        )

    return _make


@pytest.fixture
def rls(claims_factory):
    """Run a query through the RLS-enforced connection as a given role."""

    def _run(role: str, sql: str, **params):
        with rls_session(claims_factory(role)) as session:
            return session.execute(text(sql), params).scalars().all()

    return _run


@pytest.fixture(scope="session")
def fixture_batch_id() -> str:
    """A batch used by RLS and API tests. Created once, reused."""
    batch_id = str(uuid.uuid4())
    with service_session() as session:
        session.execute(
            text(
                """
                insert into public.migration_batches (id, batch_code, name, status)
                values (cast(:id as uuid), :code, 'Pytest fixture batch', 'validated')
                on conflict (batch_code) do nothing
                """
            ),
            {"id": batch_id, "code": "PYTEST-FIXTURE"},
        )
        existing = session.execute(
            text("select id from public.migration_batches where batch_code = 'PYTEST-FIXTURE'")
        ).scalar_one()
    return str(existing)
