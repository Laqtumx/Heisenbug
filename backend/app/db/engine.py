"""Postgres access: engine, sessions and RLS-aware session context.

The API uses two connection modes:

* ``service_engine`` — the trusted pipeline path. The login role owns the tables
  and carries BYPASSRLS, so batch loads and audit writes are not filtered.
* ``user_engine``    — a pooled engine that, for every request, opens a
  transaction and sets ``request.jwt.claims`` from the verified token. Postgres
  then applies the RLS policies in 005_rls.sql itself.

Keeping both means the authorisation decision is enforced by the database, not
just by Python checks in the route handlers.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

service_engine: Engine = create_engine(
    settings.sqlalchemy_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    future=True,
    # jsonb round-trips as dict already; keep raw psycopg types otherwise.
    connect_args={"options": "-c timezone=utc"},
)


@event.listens_for(service_engine, "connect")
def _register_json_codecs(dbapi_connection: Any, _record: Any) -> None:
    """Ensure jsonb columns come back as Python objects, not strings."""
    try:
        from psycopg.types.json import Jsonb  # noqa: F401
    except ImportError:  # pragma: no cover - psycopg3 is a hard requirement
        pass


user_engine: Engine = create_engine(
    settings.sqlalchemy_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    future=True,
)

ServiceSession = sessionmaker(bind=service_engine, expire_on_commit=False, future=True)


def _rls_engine_url() -> str:
    """Connection string for the RLS-enforced path.

    On Supabase this is the same URL (the platform role already lacks
    BYPASSRLS). Locally the owner is a superuser, so RLS would be skipped
    entirely; the app therefore switches to the dedicated non-superuser role
    created by db/local/900_local_app_role.sql.
    """
    if settings.rls_database_url:
        return settings.rls_database_url
    if settings.environment != "local":
        return settings.sqlalchemy_url

    from sqlalchemy.engine import make_url

    url = make_url(settings.sqlalchemy_url)
    return url.set(
        username=settings.rls_db_user,
        password=settings.rls_db_password,
    ).render_as_string(hide_password=False)


rls_engine: Engine = create_engine(
    _rls_engine_url(),
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    future=True,
)

if settings.environment == "local" and not settings.rls_database_url:

    @event.listens_for(rls_engine, "connect")
    def _assume_authenticated_role(dbapi_connection: Any, _record: Any) -> None:
        """Mirror Supabase/PostgREST: the login role immediately assumes the
        platform `authenticated` role, which is the role the RLS policies name.

        The login role is created NOINHERIT precisely so this switch is the only
        way privileges are obtained — the same arrangement Supabase uses.
        """
        with dbapi_connection.cursor() as cursor:
            cursor.execute("set role authenticated")


RlsSession = sessionmaker(bind=rls_engine, expire_on_commit=False, future=True)


@contextmanager
def service_session() -> Iterator[Session]:
    """Trusted session. Commits on success, rolls back on error."""
    session = ServiceSession()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def rls_session(claims: dict[str, Any]) -> Iterator[Session]:
    """Session where Postgres enforces RLS for the caller's JWT claims.

    ``set_config`` is called with ``is_local = true`` so the claim lives only for
    the enclosing transaction. Connection pooling therefore cannot leak one
    user's identity into another user's request.
    """
    session = RlsSession()
    try:
        session.execute(
            text("select set_config('request.jwt.claims', :claims, true)"),
            {"claims": json.dumps(claims)},
        )
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def healthcheck() -> dict[str, Any]:
    with service_session() as session:
        version = session.execute(text("select version()")).scalar_one()
        tables = session.execute(
            text(
                "select count(*) from information_schema.tables "
                "where table_schema in ('public', 'staging')"
            )
        ).scalar_one()
    return {"postgres": "ok", "version": version.split(",")[0], "tables": int(tables)}
