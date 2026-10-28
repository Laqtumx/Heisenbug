"""End-to-end ingest, profile and validate through Postgres and Mongo.

The detection test measures the rules in memory. This one exercises the real
path: bytes go in, the raw copy lands in Mongo, the frame is read back, every
rule runs, findings are persisted and the batch records its stage status and an
audit entry.

It is the only test that would catch a break in the wiring between the stages,
which is where integration faults actually live.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pandas as pd
import pytest
from sqlalchemy import text

from app.db.engine import service_session
from app.pipeline.demo_data import DemoDataGenerator, RecordCounts
from app.pipeline.ingest import create_batch, ingest_bytes, load_entity_frame
from app.pipeline.profile import profile_entity
from app.rules.engine import run_validation

pytestmark = pytest.mark.pipeline

# Fixed, not random: the fixture inserts on conflict do nothing, so a rerun
# after a crash would leave the row with its original id. A fresh random id
# would then point at a user that does not exist and the foreign key would fail.
USER_ID = "00000000-0000-4000-8000-00000000c1ea"
USER = SimpleNamespace(
    user_id=USER_ID,
    email="pytest.engineer@cleanslate.test",
    role="migration_engineer",
)


@pytest.fixture(scope="module", autouse=True)
def _pipeline_user():
    """A real app_users row.

    migration_batches.created_by is a foreign key into app_users, so the batch
    cannot be created without one. Removed afterwards.
    """
    with service_session() as session:
        session.execute(
            text(
                """
                insert into public.app_users (id, email, full_name, role)
                values (cast(:id as uuid), :email, 'Pytest Engineer', 'migration_engineer')
                on conflict (email) do nothing
                """
            ),
            {"id": USER_ID, "email": USER.email},
        )
    yield
    with service_session() as session:
        session.execute(
            text("delete from public.app_users where email = :email"),
            {"email": USER.email},
        )


@pytest.fixture(scope="module")
def small_dataset():
    counts = RecordCounts(
        suppliers=40, customers=60, items=80, categories=10, uom=8, tax_codes=6,
        chart_of_accounts=20, gl_balances=100, purchase_orders=80, invoices=200,
        inventory_stock=60, supplier_catalog=60, employees_payroll_summary=40,
    )
    return DemoDataGenerator(seed=4242, counts=counts).generate()


@pytest.fixture(scope="module")
def migrated_batch(small_dataset):
    """Ingest every entity, then validate, in one batch. Removed afterwards."""
    with service_session() as session:
        batch = create_batch(session, name="Pytest pipeline batch", user=USER)
        batch_id = str(batch["id"])

        for entity, frame in small_dataset.frames.items():
            csv_bytes = frame.to_csv(index=False).encode()
            ingest_bytes(
                session,
                batch_id=batch_id,
                entity=entity,
                file_name=f"{entity}.csv",
                data=csv_bytes,
                user=USER,
            )

        frames = {entity: load_entity_frame(batch_id, entity) for entity in small_dataset.frames}
        result = run_validation(session, batch_id=batch_id, frames=frames, user=USER)

    yield {"batch_id": batch_id, "result": result, "frames": frames}

    with service_session() as session:
        session.execute(
            text("delete from public.migration_batches where id = cast(:b as uuid)"),
            {"b": batch_id},
        )


def test_raw_copy_is_stored_in_mongo(migrated_batch, small_dataset) -> None:
    batch_id = migrated_batch["batch_id"]
    for entity in ("suppliers", "invoices"):
        frame = load_entity_frame(batch_id, entity)
        assert len(frame) == len(small_dataset.frames[entity])


def test_validation_persists_findings(migrated_batch) -> None:
    result = migrated_batch["result"]
    assert sum(result.findings_by_entity.values()) > 0

    with service_session() as session:
        stored = session.execute(
            text(
                "select count(*) from public.validation_findings "
                "where batch_id = cast(:b as uuid)"
            ),
            {"b": migrated_batch["batch_id"]},
        ).scalar_one()

    assert stored == sum(result.findings_by_entity.values())


def test_health_score_is_low_for_dirty_data(migrated_batch) -> None:
    """The demo dataset is deliberately dirty, so the score must not be high."""
    result = migrated_batch["result"]
    assert 0 <= result.health_score <= 100
    assert result.health_score < 60


def test_stage_status_is_recorded(migrated_batch) -> None:
    with service_session() as session:
        rows = session.execute(
            text(
                "select stage, status from public.migration_stages "
                "where batch_id = cast(:b as uuid) and stage in ('validate')"
            ),
            {"b": migrated_batch["batch_id"]},
        ).all()
    assert rows, "the validate stage row is missing"
    assert rows[0].status == "succeeded"


def test_validation_writes_an_audit_entry(migrated_batch) -> None:
    with service_session() as session:
        count = session.execute(
            text(
                "select count(*) from public.audit_log "
                "where batch_id = cast(:b as uuid) and action like 'validate%'"
            ),
            {"b": migrated_batch["batch_id"]},
        ).scalar_one()
    assert count > 0


def test_revalidation_replaces_findings_rather_than_stacking(migrated_batch) -> None:
    """Running validate twice must not double the findings."""
    batch_id = migrated_batch["batch_id"]
    with service_session() as session:
        first = run_validation(
            session, batch_id=batch_id, frames=migrated_batch["frames"], user=USER
        )
        second = run_validation(
            session, batch_id=batch_id, frames=migrated_batch["frames"], user=USER
        )
        stored = session.execute(
            text(
                "select count(*) from public.validation_findings "
                "where batch_id = cast(:b as uuid)"
            ),
            {"b": batch_id},
        ).scalar_one()

    first_total = sum(first.findings_by_entity.values())
    second_total = sum(second.findings_by_entity.values())
    assert first_total == second_total
    assert stored == second_total


def test_profile_reports_column_statistics(migrated_batch) -> None:
    frames = migrated_batch["frames"]
    profile = profile_entity("suppliers", frames["suppliers"])
    columns = {column.column_name: column for column in profile}
    assert "name" in columns
    assert columns["name"].distinct_count > 0
    assert 0.0 <= columns["name"].null_pct <= 100.0
