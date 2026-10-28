"""Schema migration runner.

Applies ``backend/db/local/*.sql`` first (only when the auth schema is absent,
that is, on plain Postgres) and then every ``backend/db/migrations/*.sql`` in
lexical order. Every migration is written to be idempotent, so running this
twice is safe and re-running after a change re-derives derived state such as the
role/permission matrix.

Usage:
    python -m app.db.migrate          # apply
    python -m app.db.migrate --check  # report what would run, change nothing
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import sys
from pathlib import Path

from sqlalchemy import text

from app.db.engine import service_engine

logger = logging.getLogger(__name__)

DB_DIR = Path(__file__).resolve().parents[2] / "db"
LOCAL_DIR = DB_DIR / "local"
MIGRATIONS_DIR = DB_DIR / "migrations"

LEDGER_DDL = """
create table if not exists public.schema_migrations (
  filename    text primary key,
  checksum    text not null,
  applied_at  timestamptz not null default now(),
  duration_ms integer not null default 0
)
"""


def _auth_schema_exists(conn) -> bool:
    return bool(
        conn.execute(
            text("select exists (select 1 from pg_namespace where nspname = 'auth')")
        ).scalar()
    )


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sql_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob("*.sql"))


def _apply(conn, path: Path, *, force: bool) -> tuple[bool, int]:
    """Run one file. Returns (applied, duration_ms)."""
    import time

    checksum = _checksum(path)
    existing = conn.execute(
        text("select checksum from public.schema_migrations where filename = :f"),
        {"f": path.name},
    ).scalar()

    if existing == checksum and not force:
        return False, 0
    if existing and existing != checksum and not force:
        logger.warning(
            "%s changed since it was applied (checksum mismatch); re-applying", path.name
        )

    started = time.perf_counter()
    sql = path.read_text(encoding="utf-8")
    # Run through the raw DBAPI driver: SQLAlchemy's text() treats '%' as a bind
    # parameter marker, which breaks the format('%I', ...) calls in the
    # procedural migration blocks.
    raw = conn.connection.driver_connection
    with raw.cursor() as cursor:
        cursor.execute(sql)
    duration_ms = int((time.perf_counter() - started) * 1000)
    conn.execute(
        text(
            """
            insert into public.schema_migrations (filename, checksum, duration_ms)
            values (:f, :c, :d)
            on conflict (filename) do update
              set checksum = excluded.checksum,
                  applied_at = now(),
                  duration_ms = excluded.duration_ms
            """
        ),
        {"f": path.name, "c": checksum, "d": duration_ms},
    )
    return True, duration_ms


def run(*, check: bool = False, force: bool = False) -> int:
    from app.config import settings

    applied_count = 0
    with service_engine.begin() as conn:
        conn.execute(text(LEDGER_DDL))

        # `db/local/*` recreates the Supabase contract (auth schema, platform
        # roles, a non-superuser login for the RLS path) on plain Postgres. It
        # only runs when the app is configured for local development, so a
        # Supabase deployment never sees it. Files are split into two phases:
        # `0*` runs first (the auth shim the migrations depend on), `9*` runs
        # last (needs the roles created by 001).
        pre_local: list[Path] = []
        post_local: list[Path] = []
        if settings.environment == "local":
            for path in _sql_files(LOCAL_DIR):
                (post_local if path.name.startswith("9") else pre_local).append(path)
        else:
            logger.info(
                "environment=%s; skipping local development files in %s",
                settings.environment,
                LOCAL_DIR,
            )

        targets = pre_local + _sql_files(MIGRATIONS_DIR) + post_local
        if not targets:
            logger.warning("No migration files found under %s", DB_DIR)
            return 0

        for path in targets:
            if check:
                print(f"would apply: {path.name}")
                continue
            applied, duration_ms = _apply(conn, path, force=force)
            if applied:
                applied_count += 1
                print(f"applied {path.name} ({duration_ms} ms)")
            else:
                print(f"up to date: {path.name}")

    print(f"\n{applied_count} migration(s) applied.")
    return applied_count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply CleanSlate schema migrations")
    parser.add_argument("--check", action="store_true", help="list files without applying")
    parser.add_argument("--force", action="store_true", help="re-apply even if unchanged")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    run(check=args.check, force=args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
