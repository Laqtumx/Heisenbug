#!/usr/bin/env python
"""Seed demo users and, optionally, run a full migration end to end.

Two things happen here.

First, the demo accounts are created: one per role in the RBAC matrix, so the
permission differences can be shown side by side rather than described.

Second, with ``--run-pipeline``, a batch is taken through every stage against
the dataset in ``data/legacy``. That is the fastest way to get a demo-ready
system: one command, and the dashboards have real numbers in them.

Usage:
    python scripts/seed.py
    python scripts/seed.py --run-pipeline
    python scripts/seed.py --reset
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import text  # noqa: E402

from app.db.engine import service_session  # noqa: E402
from app.pipeline.entities import ENTITIES  # noqa: E402
from app.security import auth  # noqa: E402

# One account per role. The passwords are deliberately obvious: this seeds a
# demo, and a shared secret that nobody writes down is a secret nobody can use.
DEMO_PASSWORD = "cleanslate"
DEMO_USERS = [
    {
        "email": "admin@cleanslate.test",
        "full_name": "Ada Administrator",
        "role": "admin",
    },
    {
        "email": "engineer@cleanslate.test",
        "full_name": "Mira Engineer",
        "role": "migration_engineer",
    },
    {
        "email": "finance@cleanslate.test",
        "full_name": "Frank Controller",
        "role": "finance_controller",
    },
    {
        "email": "inventory@cleanslate.test",
        "full_name": "Iris Manager",
        "role": "inventory_manager",
    },
    {
        "email": "supplier@cleanslate.test",
        "full_name": "Sam Supplier",
        "role": "supplier",
    },
]


def banner(message: str) -> None:
    print(f"\n=== {message} ===")


def seed_users(session) -> dict[str, str]:
    """Create the demo accounts. Safe to run repeatedly."""
    ids: dict[str, str] = {}
    password_hash = auth.hash_password(DEMO_PASSWORD)

    for user in DEMO_USERS:
        row = session.execute(
            text(
                """
                insert into public.app_users (email, full_name, password_hash, role)
                values (:email, :full_name, :password_hash, :role)
                on conflict (email) do update
                  set full_name = excluded.full_name,
                      password_hash = excluded.password_hash,
                      role = excluded.role,
                      is_active = true
                returning id
                """
            ),
            {
                "email": user["email"],
                "full_name": user["full_name"],
                "password_hash": password_hash,
                "role": user["role"],
            },
        ).scalar_one()
        ids[user["role"]] = str(row)

    return ids


def link_supplier_account(session, supplier_user_id: str) -> str | None:
    """Point the supplier account at a real supplier.

    A supplier sees only their own purchase orders and catalogue, and the RLS
    policy decides that by comparing the row's supplier_code to the claim in the
    token. Without this link the external account would authenticate correctly
    and then see nothing, which is right but makes for a dull demo.
    """
    # Pick the supplier with the most purchase orders, so the external demo
    # account lands on one that actually has data to show. Linking to an
    # arbitrary supplier can land on one with no orders, which makes a working
    # policy look like a broken page.
    row = session.execute(
        text(
            """
            select s.id, s.supplier_code
              from public.suppliers s
              left join public.purchase_orders po on po.supplier_code = s.supplier_code
             group by s.id, s.supplier_code
             order by count(po.id) desc, s.supplier_code
             limit 1
            """
        )
    ).first()
    if row is None:
        # No target data yet. Fall back to any supplier so the account still
        # links and the FK holds.
        row = session.execute(
            text("select id, supplier_code from public.suppliers order by supplier_code limit 1")
        ).first()
    if row is None:
        return None
    supplier_id = row[0]
    session.execute(
        text("update public.app_users set supplier_id = :sid where id = cast(:uid as uuid)"),
        {"sid": supplier_id, "uid": supplier_user_id},
    )
    return str(supplier_id)


def reset(session) -> None:
    """Remove every batch and its loaded rows.

    Only the pipeline data is cleared. Users and roles are left alone, because
    re-seeding them is instant and deleting them would invalidate any token
    already issued.
    """
    session.execute(text("delete from public.migration_batches"))
    print("cleared all migration batches and their loaded rows")


def run_pipeline(session, user, data_dir: Path) -> dict:
    """Take a batch through every stage of the pipeline."""
    from app.pipeline.clean import run_clean
    from app.pipeline.ingest import create_batch, ingest_bytes
    from app.pipeline.load import run_dry_run, run_load
    from app.pipeline.profile import run_profile
    from app.rules.engine import run_validation

    missing = [spec.name for spec in ENTITIES if not (data_dir / f"{spec.name}.csv").exists()]
    if missing:
        raise SystemExit(
            f"missing demo files for {missing}. Run 'make demo-data' first."
        )

    batch = create_batch(session, name="Demo migration", user=user)
    batch_id = str(batch["id"])
    print(f"batch {batch['batch_code']} ({batch_id[:8]})")

    frames: dict = {}
    banner("INGEST")
    for spec in ENTITIES:
        path = data_dir / f"{spec.name}.csv"
        result = ingest_bytes(
            session,
            batch_id=batch_id,
            entity=spec.name,
            file_name=path.name,
            data=path.read_bytes(),
            user=user,
        )
        print(f"  {spec.name:28} {result.row_count:>7,} rows")
        frames[spec.name] = result

    from app.pipeline.ingest import load_entity_frame

    frames = {name: load_entity_frame(batch_id, name) for name in frames}

    banner("PROFILE")
    started = time.perf_counter()
    profile = run_profile(session, batch_id=batch_id, frames=frames, user=user)
    print(f"  {profile.total_columns:,} columns in {time.perf_counter() - started:.1f}s")

    banner("VALIDATE")
    started = time.perf_counter()
    validation = run_validation(session, batch_id=batch_id, frames=frames, user=user)
    print(f"  health score {validation.health_score}  findings {sum(validation.findings_by_severity.values()):,}")
    print(f"  by severity {validation.findings_by_severity}")
    print(f"  {time.perf_counter() - started:.1f}s")

    banner("CLEAN")
    started = time.perf_counter()
    cleaned, summary = run_clean(session, batch_id=batch_id, frames=frames, user=user)
    print(f"  auto-fixed {summary.auto_fixed:,}  queued {summary.queued_for_review:,}  "
          f"archived {summary.archived:,}  merged {summary.duplicates_merged:,}")
    print(f"  {time.perf_counter() - started:.1f}s")

    banner("DRY RUN")
    started = time.perf_counter()
    dry = run_dry_run(session, batch_id=batch_id, frames=cleaned, user=user)
    print(f"  balanced {dry.balanced}  {time.perf_counter() - started:.1f}s")
    print(f"  {dry.counts}")

    banner("LOAD")
    started = time.perf_counter()
    load = run_load(session, batch_id=batch_id, frames=cleaned, user=user)
    print(f"  balanced {load.balanced}  {time.perf_counter() - started:.1f}s")

    from app.pipeline.load import migration_health_score

    health = migration_health_score(session, batch_id)
    banner("HEALTH")
    print(f"  score {health['health_score']}  go-live ready {health['go_live_ready']}")
    for item in health["checklist"]:
        print(f"  [{'x' if item['passed'] else ' '}] {item['item']}: {item['detail']}")

    return {"batch_id": batch_id, "health": health, "clean": summary}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-pipeline", action="store_true", help="run a full migration")
    parser.add_argument("--reset", action="store_true", help="delete existing batches first")
    parser.add_argument(
        "--data-dir", type=Path, default=ROOT / "data" / "legacy", help="legacy CSV directory"
    )
    args = parser.parse_args()

    with service_session() as session:
        if args.reset:
            banner("RESET")
            reset(session)

        banner("USERS")
        ids = seed_users(session)
        supplier_id = link_supplier_account(session, ids["supplier"])
        for user in DEMO_USERS:
            note = f"  (supplier_id {supplier_id[:8]})" if user["role"] == "supplier" and supplier_id else ""
            print(f"  {user['email']:32} {user['role']:20} password: {DEMO_PASSWORD}{note}")

        if args.run_pipeline:
            engineer = SimpleNamespace(
                user_id=ids["migration_engineer"],
                email="engineer@cleanslate.test",
                role="migration_engineer",
            )
            run_pipeline(session, engineer, args.data_dir)

    banner("DONE")
    print("  API docs   http://localhost:8000/docs")
    print("  Sign in as any account above with the password shown.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
