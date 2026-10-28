"""Stages 6-7 — dry run, load, reconciliation and rollback.

These run against real Postgres, because the whole point of the stage is what
the database does: the unique constraints, the encrypted columns, the delete
that makes a reload idempotent and the rollback that undoes it.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.db import mongo
from app.db.engine import service_session
from app.pipeline.clean import run_clean
from app.pipeline.demo_data import DemoDataGenerator, RecordCounts
from app.pipeline.entities import ENTITIES
from app.pipeline.ingest import create_batch, ingest_bytes, load_entity_frame
from app.pipeline.load import (
    migration_health_score,
    latest_reconciliation,
    loadable_frames,
    rollback_batch,
    run_dry_run,
    run_load,
)
from app.pipeline.target import MONEY_COLUMNS, frame_to_rows, frame_totals, row_totals
from app.rules.engine import run_validation

pytestmark = pytest.mark.pipeline

USER_ID = "00000000-0000-4000-8000-00000000c1ec"
USER = SimpleNamespace(
    user_id=USER_ID,
    email="pytest.loader@cleanslate.test",
    role="migration_engineer",
)


@pytest.fixture(scope="module", autouse=True)
def _loader_user():
    with service_session() as session:
        session.execute(
            text(
                """
                insert into public.app_users (id, email, full_name, role)
                values (cast(:id as uuid), :email, 'Pytest Loader', 'migration_engineer')
                on conflict (email) do nothing
                """
            ),
            {"id": USER_ID, "email": USER.email},
        )
    yield
    with service_session() as session:
        session.execute(
            text("delete from public.app_users where email = :email"), {"email": USER.email}
        )


def _small_batch(name: str) -> tuple[str, dict]:
    counts = RecordCounts(
        suppliers=60, customers=100, items=150, categories=12, uom=8, tax_codes=8,
        chart_of_accounts=20, gl_balances=120, purchase_orders=100, invoices=250,
        inventory_stock=80, supplier_catalog=80, employees_payroll_summary=40,
    )
    dataset = DemoDataGenerator(seed=11, counts=counts).generate()
    with service_session() as session:
        batch = create_batch(session, name=name, user=USER)
        batch_id = str(batch["id"])
        for entity, frame in dataset.frames.items():
            ingest_bytes(
                session,
                batch_id=batch_id,
                entity=entity,
                file_name=f"{entity}.csv",
                data=frame.to_csv(index=False).encode(),
                user=USER,
            )
        frames = {e: load_entity_frame(batch_id, e) for e in dataset.frames}
        run_validation(session, batch_id=batch_id, frames=frames, user=USER)
        cleaned, _ = run_clean(session, batch_id=batch_id, frames=frames, user=USER)
    return batch_id, cleaned


def _drop(batch_id: str) -> None:
    with service_session() as session:
        session.execute(
            text("delete from public.migration_batches where id = cast(:b as uuid)"),
            {"b": batch_id},
        )
    mongo.collection(mongo.QUARANTINE).delete_many({"batch_id": batch_id})


@pytest.fixture(scope="module")
def loaded():
    batch_id, cleaned = _small_batch("pytest load batch")
    with service_session() as session:
        dry = run_dry_run(session, batch_id=batch_id, frames=cleaned, user=USER)
    with service_session() as session:
        load = run_load(session, batch_id=batch_id, frames=cleaned, user=USER)
    yield {"batch_id": batch_id, "cleaned": cleaned, "dry": dry, "load": load}
    _drop(batch_id)


# -------------------------------------------------------------- dry run -----


def test_dry_run_reconciles_every_entity(loaded) -> None:
    assert loaded["dry"].balanced
    assert loaded["dry"].entities


def test_dry_run_writes_to_staging_not_the_target(loaded) -> None:
    batch_id = loaded["batch_id"]
    with service_session() as session:
        staged = session.execute(
            text("select count(*) from staging.suppliers where batch_id = cast(:b as uuid)"),
            {"b": batch_id},
        ).scalar_one()
    assert staged > 0


def test_dry_run_counts_are_stable_across_runs(loaded) -> None:
    """The same input must reconcile the same way twice."""
    batch_id = loaded["batch_id"]
    with service_session() as session:
        again = run_dry_run(session, batch_id=batch_id, frames=loaded["cleaned"], user=USER)
    assert again.counts == loaded["dry"].counts
    assert again.balanced


def test_staging_has_the_same_unique_keys_as_the_target() -> None:
    """Otherwise the dry run passes data the real load will reject."""
    with service_session() as session:
        missing = session.execute(
            text(
                """
                select tc.table_name, tc.constraint_name
                  from information_schema.table_constraints tc
                 where tc.table_schema = 'public'
                   and tc.constraint_type = 'unique'
                   and exists (
                     select 1 from information_schema.tables st
                      where st.table_schema = 'staging' and st.table_name = tc.table_name
                   )
                   and not exists (
                     select 1 from pg_indexes pi
                      where pi.schemaname = 'staging'
                        and pi.tablename = tc.table_name
                        and pi.indexdef like 'CREATE UNIQUE INDEX%'
                   )
                """
            )
        ).all()
    assert missing == []


# ----------------------------------------------------------------- load -----


def test_load_reconciles_every_entity(loaded) -> None:
    assert loaded["load"].balanced


def test_load_writes_to_the_target_tables(loaded) -> None:
    batch_id = loaded["batch_id"]
    with service_session() as session:
        for entity in ("categories", "suppliers", "items", "invoices"):
            count = session.execute(
                text(f"select count(*) from public.{entity} where batch_id = cast(:b as uuid)"),
                {"b": batch_id},
            ).scalar_one()
            assert count > 0, entity


def test_every_record_is_accounted_for(loaded) -> None:
    """Nothing may vanish between ingest and load."""
    counts = loaded["load"].counts
    assert counts["fully_accounted"], counts
    assert counts["records_accounted_for"] == counts["records_in"]


def test_sensitive_columns_are_encrypted_at_rest(loaded) -> None:
    batch_id = loaded["batch_id"]
    with service_session() as session:
        row = session.execute(
            text(
                """
                select gstin_encrypted, gstin_hash
                  from public.suppliers
                 where batch_id = cast(:b as uuid) and gstin_encrypted is not null
                 limit 1
                """
            ),
            {"b": batch_id},
        ).mappings().first()
    assert row is not None
    # A Fernet token, not a plaintext tax id.
    assert row["gstin_encrypted"].startswith("gAAAAA")
    assert row["gstin_hash"] and len(row["gstin_hash"]) == 64


def test_encryption_round_trips(loaded) -> None:
    from app.security.crypto import decrypt

    batch_id = loaded["batch_id"]
    with service_session() as session:
        row = session.execute(
            text(
                "select gstin_encrypted, gstin_hash from public.suppliers "
                "where batch_id = cast(:b as uuid) and gstin_encrypted is not null limit 1"
            ),
            {"b": batch_id},
        ).mappings().first()
    plaintext = decrypt(row["gstin_encrypted"])
    from app.security.crypto import blind_index

    assert blind_index(plaintext) == row["gstin_hash"]


def test_blocked_records_are_not_loaded(loaded) -> None:
    """A record with an open blocker must stay out of the target tables."""
    batch_id = loaded["batch_id"]
    with service_session() as session:
        blocked = {
            (str(e), str(r))
            for e, r in session.execute(
                text(
                    """
                    select entity, record_id from public.validation_findings
                     where batch_id = cast(:b as uuid) and severity = 'blocker'
                       and status in ('open', 'pending_review')
                    """
                ),
                {"b": batch_id},
            ).all()
        }
        for entity, record_id in blocked:
            spec = next(s for s in ENTITIES if s.name == entity)
            present = session.execute(
                text(
                    f"select count(*) from public.{spec.target_table} "
                    "where batch_id = cast(:b as uuid) and source_ref = :ref"
                ),
                {"b": batch_id, "ref": record_id},
            ).scalar_one()
            assert present == 0, f"{entity}/{record_id} loaded despite a blocker"


def test_load_is_idempotent(loaded) -> None:
    """Loading twice must replace, not duplicate."""
    batch_id = loaded["batch_id"]
    with service_session() as session:
        before = session.execute(
            text("select count(*) from public.suppliers where batch_id = cast(:b as uuid)"),
            {"b": batch_id},
        ).scalar_one()
        second = run_load(session, batch_id=batch_id, frames=loaded["cleaned"], user=USER)
        after = session.execute(
            text("select count(*) from public.suppliers where batch_id = cast(:b as uuid)"),
            {"b": batch_id},
        ).scalar_one()
    assert after == before
    assert second.balanced


def test_load_refuses_when_the_dry_run_did_not_reconcile() -> None:
    batch_id, cleaned = _small_batch("pytest unbalanced batch")
    try:
        with service_session() as session:
            session.execute(
                text(
                    """
                    insert into public.reconciliation_reports (
                        batch_id, phase, counts, balanced
                    ) values (cast(:b as uuid), 'dry_run', '{}'::jsonb, false)
                    """
                ),
                {"b": batch_id},
            )
        with service_session() as session:
            with pytest.raises(ValueError, match="did not reconcile"):
                run_load(session, batch_id=batch_id, frames=cleaned, user=USER)
    finally:
        _drop(batch_id)


def test_load_refuses_without_a_dry_run() -> None:
    batch_id, cleaned = _small_batch("pytest no dry run batch")
    try:
        with service_session() as session:
            with pytest.raises(ValueError, match="no dry run"):
                run_load(session, batch_id=batch_id, frames=cleaned, user=USER)
    finally:
        _drop(batch_id)


def test_load_writes_audit_and_stage_records(loaded) -> None:
    batch_id = loaded["batch_id"]
    with service_session() as session:
        # At least one, not exactly one: another test reloads this batch, and
        # every load attempt is a separate event an auditor wants recorded.
        audit_count = session.execute(
            text(
                "select count(*) from public.audit_log "
                "where batch_id = cast(:b as uuid) and action = 'load.completed'"
            ),
            {"b": batch_id},
        ).scalar_one()
        stage_status = session.execute(
            text(
                "select status from public.migration_stages "
                "where batch_id = cast(:b as uuid) and stage = 'load'"
            ),
            {"b": batch_id},
        ).scalar_one()
    assert audit_count >= 1
    assert stage_status == "succeeded"


# ------------------------------------------------------- reconciliation -----


def test_money_totals_survive_the_round_trip(loaded) -> None:
    """The figures a finance controller checks must match to the cent."""
    for entity, recon in loaded["load"].entities.items():
        assert recon.totals_match, f"{entity}: {recon.totals_in} != {recon.totals_out}"


def test_checksums_are_recorded_per_entity(loaded) -> None:
    for entity, recon in loaded["load"].entities.items():
        assert recon.checksum, entity
        assert len(recon.checksum) == 64


def test_reconciliation_report_is_persisted(loaded) -> None:
    with service_session() as session:
        report = latest_reconciliation(session, loaded["batch_id"], phase="load")
    assert report["balanced"] is True
    assert report["counts"]["records_loaded"] > 0
    assert report["per_entity"]


def test_frame_totals_match_row_totals_for_every_money_entity() -> None:
    """The two ways of summing the same data must agree."""
    counts = RecordCounts(
        suppliers=20, customers=20, items=30, categories=6, uom=6, tax_codes=6,
        chart_of_accounts=10, gl_balances=40, purchase_orders=30, invoices=50,
        inventory_stock=20, supplier_catalog=20, employees_payroll_summary=10,
    )
    dataset = DemoDataGenerator(seed=3, counts=counts).generate()
    for spec in ENTITIES:
        frame = dataset.frames.get(spec.name)
        if frame is None or spec.name not in MONEY_COLUMNS:
            continue
        rows = frame_to_rows(spec, frame, "00000000-0000-4000-8000-000000000000")
        assert frame_totals(spec, frame) == row_totals(spec, rows), spec.name


# ------------------------------------------------------------- rollback -----


def test_rollback_removes_every_row_the_batch_loaded() -> None:
    batch_id, cleaned = _small_batch("pytest rollback batch")
    try:
        with service_session() as session:
            run_dry_run(session, batch_id=batch_id, frames=cleaned, user=USER)
        with service_session() as session:
            run_load(session, batch_id=batch_id, frames=cleaned, user=USER)
        with service_session() as session:
            rollback_batch(session, batch_id=batch_id, user=USER)

        with service_session() as session:
            remaining = 0
            for spec in ENTITIES:
                remaining += session.execute(
                    text(
                        f"select count(*) from public.{spec.target_table} "
                        "where batch_id = cast(:b as uuid)"
                    ),
                    {"b": batch_id},
                ).scalar_one()
            status = session.execute(
                text("select status from public.migration_batches where id = cast(:b as uuid)"),
                {"b": batch_id},
            ).scalar_one()
        assert remaining == 0
        assert status == "rolled_back"
    finally:
        _drop(batch_id)


def test_rollback_is_safe_to_repeat() -> None:
    batch_id, cleaned = _small_batch("pytest repeat rollback batch")
    try:
        with service_session() as session:
            run_dry_run(session, batch_id=batch_id, frames=cleaned, user=USER)
        with service_session() as session:
            run_load(session, batch_id=batch_id, frames=cleaned, user=USER)
        with service_session() as session:
            first = rollback_batch(session, batch_id=batch_id, user=USER)
        with service_session() as session:
            second = rollback_batch(session, batch_id=batch_id, user=USER)
        assert sum(first.values()) > 0
        assert sum(second.values()) == 0
    finally:
        _drop(batch_id)


def test_rollback_does_not_touch_another_batch() -> None:
    """Rollback is scoped to one batch_id, never to a table."""
    batch_a, cleaned_a = _small_batch("pytest rollback A")
    batch_b, cleaned_b = _small_batch("pytest rollback B")
    try:
        for batch_id, frames in ((batch_a, cleaned_a), (batch_b, cleaned_b)):
            with service_session() as session:
                run_dry_run(session, batch_id=batch_id, frames=frames, user=USER)
            with service_session() as session:
                run_load(session, batch_id=batch_id, frames=frames, user=USER)

        with service_session() as session:
            rollback_batch(session, batch_id=batch_a, user=USER)
            survivors = session.execute(
                text("select count(*) from public.suppliers where batch_id = cast(:b as uuid)"),
                {"b": batch_b},
            ).scalar_one()
        assert survivors > 0
    finally:
        _drop(batch_a)
        _drop(batch_b)


# ----------------------------------------------------------- health score ---


def test_health_score_reports_a_checklist(loaded) -> None:
    with service_session() as session:
        score = migration_health_score(session, loaded["batch_id"])
    assert 0 <= score["health_score"] <= 100
    assert score["checklist"]
    assert isinstance(score["go_live_ready"], bool)


def test_go_live_is_blocked_while_blockers_remain(loaded) -> None:
    """A loaded batch with open blockers is not ready to sign off."""
    with service_session() as session:
        score = migration_health_score(session, loaded["batch_id"])
    if score["open_blockers"] > 0:
        assert score["go_live_ready"] is False
