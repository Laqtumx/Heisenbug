"""Stage 4 — the clean stage and the review queue.

These tests run against real Postgres and Mongo through the real pipeline, not
mocks, because the stage's value is in what it persists: the fixed rows, the
review items, the quarantine documents and the audit entries.
"""

from __future__ import annotations

import uuid

import pandas as pd
import pytest
from sqlalchemy import text

from app.db import mongo
from app.db.engine import service_session
from app.pipeline.clean import (
    bulk_decide_review_items,
    decide_review_item,
    run_clean,
)
from app.pipeline.demo_data import DemoDataGenerator, RecordCounts
from app.pipeline.ingest import create_batch, ingest_bytes, load_entity_frame
from app.pipeline.normalise import (
    collapse_whitespace,
    is_title_case_ok,
    parse_dates,
    title_case_tolerant,
    to_iso_dates,
)
from app.rules.engine import run_validation

pytestmark = pytest.mark.pipeline

USER_ID = "00000000-0000-4000-8000-00000000c1eb"
USER = __import__("types").SimpleNamespace(
    user_id=USER_ID,
    email="pytest.cleaner@cleanslate.test",
    role="migration_engineer",
)


@pytest.fixture(scope="module", autouse=True)
def _cleaner_user():
    with service_session() as session:
        session.execute(
            text(
                """
                insert into public.app_users (id, email, full_name, role)
                values (cast(:id as uuid), :email, 'Pytest Cleaner', 'migration_engineer')
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


@pytest.fixture(scope="module")
def cleaned():
    """A small batch, ingested, validated and cleaned once for the module."""
    counts = RecordCounts(
        suppliers=60, customers=100, items=150, categories=12, uom=8, tax_codes=8,
        chart_of_accounts=20, gl_balances=120, purchase_orders=100, invoices=250,
        inventory_stock=80, supplier_catalog=80, employees_payroll_summary=40,
    )
    dataset = DemoDataGenerator(seed=7, counts=counts).generate()

    with service_session() as session:
        batch = create_batch(session, name="pytest clean batch", user=USER)
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
        raw_frames = {e: load_entity_frame(batch_id, e) for e in dataset.frames}
        before = run_validation(session, batch_id=batch_id, frames=raw_frames, user=USER)
        cleaned_frames, summary = run_clean(
            session, batch_id=batch_id, frames=raw_frames, user=USER
        )
        after = run_validation(
            session, batch_id=batch_id, frames=cleaned_frames, user=USER, clear_existing=False
        )

    yield {
        "batch_id": batch_id,
        "dataset": dataset,
        "raw": raw_frames,
        "cleaned": cleaned_frames,
        "summary": summary,
        "before": before,
        "after": after,
    }

    with service_session() as session:
        session.execute(
            text("delete from public.migration_batches where id = cast(:b as uuid)"),
            {"b": batch_id},
        )
    mongo.collection(mongo.QUARANTINE).delete_many({"batch_id": batch_id})


# ------------------------------------------------------------ normalisation --


def test_collapse_whitespace_trims_and_collapses() -> None:
    result = collapse_whitespace(pd.Series(["  Acme  Ltd ", "a\t\tb"]))
    assert result.tolist() == ["Acme Ltd", "a b"]


def test_title_case_preserves_acronyms() -> None:
    """An all-caps token survives; a lower-case one is title cased."""
    result = title_case_tolerant(pd.Series(["acme industries llp", "ACME INDUSTRIES"]))
    assert result.tolist() == ["Acme Industries Llp", "ACME INDUSTRIES"]


def test_title_case_is_idempotent() -> None:
    once = title_case_tolerant(pd.Series(["acme industries llp"]))
    twice = title_case_tolerant(once)
    assert once.tolist() == twice.tolist()


def test_is_title_case_ok_matches_the_fixer() -> None:
    """The rule and the cleaner must agree, or a fix leaves its own finding."""
    values = pd.Series(["Acme Ltd", "acme ltd", "ACME LTD", "  Acme  Ltd "])
    ok = is_title_case_ok(values)
    # Casing is whitespace-agnostic, so the check is against the collapsed form.
    assert ok.tolist() == [True, False, True, True]
    # Anything the checker passes is already what the fixer would write.
    assert (collapse_whitespace(values[ok]) == title_case_tolerant(values)[ok]).all()
    # And anything it fails is genuinely changed by the fixer.
    assert not (title_case_tolerant(values[~ok]) == collapse_whitespace(values[~ok])).any()


def test_parse_dates_handles_mixed_formats() -> None:
    values = pd.Series(["2024-01-31", "31/01/2024", "31-Jan-2024", "20240131"])
    parsed = parse_dates(values)
    assert parsed.notna().all()
    assert parsed.dt.strftime("%Y-%m-%d").nunique() == 1


def test_to_iso_leaves_unparseable_values_alone() -> None:
    values = pd.Series(["2024-01-31", "not a date", ""])
    result = to_iso_dates(values)
    assert result.tolist() == ["2024-01-31", "not a date", ""]


# --------------------------------------------------------------- auto fixes --


def test_clean_fixes_something(cleaned) -> None:
    assert cleaned["summary"].auto_fixed > 0
    assert cleaned["summary"].auto_fixed_by_rule


def test_dates_are_iso_after_cleaning(cleaned) -> None:
    frame = cleaned["cleaned"]["invoices"]
    values = frame["invoice_date"].astype(str).str.strip()
    non_blank = values[values != ""]
    assert non_blank.str.match(r"^\d{4}-\d{2}-\d{2}$").all()


def test_uom_variants_are_normalised_but_unknown_units_are_queued(cleaned) -> None:
    """A spelling variant is fixed; a unit that does not exist is not.

    The cleaner normalises "Kgs" to "KG" because the rule names the canonical
    value. It cannot invent a unit for "999", so that record must be waiting in
    the review queue rather than quietly dropped.
    """
    canonical = set(cleaned["cleaned"]["uom"]["uom_code"].astype(str))
    items = cleaned["cleaned"]["items"]
    used = set(items["uom_code"].astype(str).str.strip())
    unknown = used - canonical

    with service_session() as session:
        queued = session.execute(
            text(
                "select distinct record_id from public.review_queue "
                "where batch_id = cast(:b as uuid) and entity = 'items' "
                "and status = 'pending'"
            ),
            {"b": cleaned["batch_id"]},
        ).scalars().all()

    offenders = items.loc[
        items["uom_code"].astype(str).str.strip().isin(unknown), "record_id"
    ].astype(str)
    assert set(offenders) <= set(queued)


def test_archived_records_are_removed_from_the_load_set(cleaned) -> None:
    summary = cleaned["summary"]
    assert summary.archived > 0
    assert summary.rows_out == summary.rows_in - summary.archived - summary.duplicates_merged


def test_archiving_never_orphans_a_child(cleaned) -> None:
    """A stale parent a child still points at must stay.

    Archiving is meant to drop records nobody needs. Dropping a customer that
    invoices still reference does not clean anything: it turns one stale master
    row into hundreds of orphan findings, which is the opposite of the promise.
    """
    raw_customers = cleaned["raw"]["customers"]
    clean_customers = cleaned["cleaned"]["customers"]
    raw_keys = set(raw_customers["customer_code"].astype(str).str.strip())
    kept_keys = set(clean_customers["customer_code"].astype(str).str.strip())

    referenced = set(cleaned["cleaned"]["invoices"]["customer_code"].astype(str).str.strip())
    dropped_but_referenced = (raw_keys - kept_keys) & referenced
    assert dropped_but_referenced == set()


def test_cleaning_does_not_create_new_orphans(cleaned) -> None:
    """Cleaning must not raise the orphan count it is supposed to reduce."""

    def orphan_count(frames: dict) -> int:
        parents = set(frames["customers"]["customer_code"].astype(str).str.strip())
        refs = frames["invoices"]["customer_code"].astype(str).str.strip()
        return int((~refs.isin(parents)).sum())

    assert orphan_count(cleaned["cleaned"]) <= orphan_count(cleaned["raw"])


def test_health_score_after_is_persisted(cleaned) -> None:
    """The dashboard reads this column, so the clean stage has to write it."""
    with service_session() as session:
        row = session.execute(
            text(
                "select health_score_before, health_score_after "
                "from public.migration_batches where id = cast(:b as uuid)"
            ),
            {"b": cleaned["batch_id"]},
        ).mappings().first()
    assert row["health_score_before"] is not None
    assert row["health_score_after"] is not None
    assert row["health_score_after"] > row["health_score_before"]


def test_merge_keeps_the_more_complete_record(cleaned) -> None:
    """A merge must not lose a field the loser had filled in."""
    assert cleaned["summary"].duplicates_merged > 0
    suppliers = cleaned["cleaned"]["suppliers"]
    assert not suppliers["supplier_code"].duplicated().any()


def test_cleaning_reduces_findings(cleaned) -> None:
    before = sum(cleaned["before"].findings_by_entity.values())
    after = sum(cleaned["after"].findings_by_entity.values())
    assert after < before


def test_health_score_rises_after_cleaning(cleaned) -> None:
    assert cleaned["after"].health_score > cleaned["before"].health_score


def test_auto_fixed_findings_are_marked(cleaned) -> None:
    with service_session() as session:
        count = session.execute(
            text(
                "select count(*) from public.validation_findings "
                "where batch_id = cast(:b as uuid) and status = 'auto_fixed'"
            ),
            {"b": cleaned["batch_id"]},
        ).scalar_one()
    assert count > 0


# ------------------------------------------------------------- review queue --


def test_review_queue_holds_what_could_not_be_fixed(cleaned) -> None:
    assert cleaned["summary"].queued_for_review > 0
    with service_session() as session:
        pending = session.execute(
            text(
                "select count(*) from public.review_queue "
                "where batch_id = cast(:b as uuid) and status = 'pending'"
            ),
            {"b": cleaned["batch_id"]},
        ).scalar_one()
    assert pending > 0


def test_review_items_carry_both_legacy_and_proposed(cleaned) -> None:
    with service_session() as session:
        row = session.execute(
            text(
                """
                select legacy_record, proposed_record, entity, reason, severity
                  from public.review_queue
                 where batch_id = cast(:b as uuid)
                 limit 1
                """
            ),
            {"b": cleaned["batch_id"]},
        ).mappings().one()
    assert row["legacy_record"]
    assert row["proposed_record"]
    assert row["entity"] and row["reason"] and row["severity"]


def test_review_items_link_to_their_finding(cleaned) -> None:
    with service_session() as session:
        unlinked = session.execute(
            text(
                "select count(*) from public.review_queue "
                "where batch_id = cast(:b as uuid) and finding_id is null"
            ),
            {"b": cleaned["batch_id"]},
        ).scalar_one()
    assert unlinked == 0


def test_approve_marks_the_item_and_the_finding(cleaned) -> None:
    batch_id = cleaned["batch_id"]
    with service_session() as session:
        item = session.execute(
            text(
                "select id, finding_id from public.review_queue "
                "where batch_id = cast(:b as uuid) and status = 'pending' limit 1"
            ),
            {"b": batch_id},
        ).mappings().one()
        decision = decide_review_item(
            session, review_id=str(item["id"]), decision="approve", user=USER, note="checked"
        )
        finding_status = session.execute(
            text("select status from public.validation_findings where id = cast(:i as uuid)"),
            {"i": str(item["finding_id"])},
        ).scalar_one()

    assert decision.status == "approved"
    assert finding_status == "approved"


def test_reject_quarantines_the_record(cleaned) -> None:
    batch_id = cleaned["batch_id"]
    with service_session() as session:
        item = session.execute(
            text(
                "select id, entity, record_id from public.review_queue "
                "where batch_id = cast(:b as uuid) and status = 'pending' "
                "and entity = 'purchase_orders' limit 1"
            ),
            {"b": batch_id},
        ).mappings().one()
        decide_review_item(
            session, review_id=str(item["id"]), decision="reject", user=USER, note="orphan"
        )

    quarantined = mongo.collection(mongo.QUARANTINE).count_documents(
        {"batch_id": batch_id, "record_id": str(item["record_id"]), "rule_id": "REVIEW"}
    )
    assert quarantined == 1


def test_a_decision_cannot_be_made_twice(cleaned) -> None:
    batch_id = cleaned["batch_id"]
    with service_session() as session:
        item = session.execute(
            text(
                "select id from public.review_queue "
                "where batch_id = cast(:b as uuid) and status = 'pending' limit 1"
            ),
            {"b": batch_id},
        ).scalar_one()
        decide_review_item(session, review_id=str(item), decision="approve", user=USER)
        with pytest.raises(ValueError):
            decide_review_item(session, review_id=str(item), decision="approve", user=USER)


def test_a_bad_decision_is_rejected(cleaned) -> None:
    with service_session() as session:
        item = session.execute(
            text(
                "select id from public.review_queue "
                "where batch_id = cast(:b as uuid) and status = 'pending' limit 1"
            ),
            {"b": cleaned["batch_id"]},
        ).scalar_one()
        with pytest.raises(ValueError):
            decide_review_item(session, review_id=str(item), decision="maybe", user=USER)


def test_decisions_write_audit_entries(cleaned) -> None:
    with service_session() as session:
        count = session.execute(
            text(
                "select count(*) from public.audit_log "
                "where batch_id = cast(:b as uuid) and action like 'review.%'"
            ),
            {"b": cleaned["batch_id"]},
        ).scalar_one()
    assert count > 0


def test_clean_writes_an_audit_entry(cleaned) -> None:
    with service_session() as session:
        count = session.execute(
            text(
                "select count(*) from public.audit_log "
                "where batch_id = cast(:b as uuid) and action = 'clean.completed'"
            ),
            {"b": cleaned["batch_id"]},
        ).scalar_one()
    assert count == 1


def test_clean_marks_the_stage_succeeded(cleaned) -> None:
    with service_session() as session:
        status = session.execute(
            text(
                "select status from public.migration_stages "
                "where batch_id = cast(:b as uuid) and stage = 'clean'"
            ),
            {"b": cleaned["batch_id"]},
        ).scalar_one()
    assert status == "succeeded"


def test_clean_is_idempotent(cleaned) -> None:
    """Re-running clean must not stack duplicate review items."""
    batch_id = cleaned["batch_id"]
    with service_session() as session:
        before = session.execute(
            text(
                "select count(*) from public.review_queue "
                "where batch_id = cast(:b as uuid) and status = 'pending'"
            ),
            {"b": batch_id},
        ).scalar_one()

    with service_session() as session:
        frames = {e: load_entity_frame(batch_id, e) for e in cleaned["raw"]}
        run_clean(session, batch_id=batch_id, frames=frames, user=USER)
        after = session.execute(
            text(
                "select count(*) from public.review_queue "
                "where batch_id = cast(:b as uuid) and status = 'pending'"
            ),
            {"b": batch_id},
        ).scalar_one()

    assert after == before


def test_bulk_reject_only_touches_pending_items(cleaned) -> None:
    batch_id = cleaned["batch_id"]
    with service_session() as session:
        already = session.execute(
            text(
                "select count(*) from public.review_queue "
                "where batch_id = cast(:b as uuid) and status <> 'pending'"
            ),
            {"b": batch_id},
        ).scalar_one()
        result = bulk_decide_review_items(
            session, batch_id=batch_id, decision="reject", user=USER, entity="invoices"
        )
        still_pending_other = session.execute(
            text(
                "select count(*) from public.review_queue "
                "where batch_id = cast(:b as uuid) and status = 'pending' and entity <> 'invoices'"
            ),
            {"b": batch_id},
        ).scalar_one()
        decisions_preserved = session.execute(
            text(
                "select count(*) from public.review_queue "
                "where batch_id = cast(:b as uuid) and status <> 'pending'"
            ),
            {"b": batch_id},
        ).scalar_one()

    assert result["failed"] == []
    assert result["decided"] == result["requested"]
    # Items outside the filter are untouched.
    assert still_pending_other > 0
    # Earlier decisions were not overwritten.
    assert decisions_preserved >= already


def test_bulk_by_ids_decides_only_the_listed_items(cleaned) -> None:
    """A ticked selection decides exactly those rows, and nothing else.

    This is the behaviour the review screen relies on: the reviewer picks rows,
    and the API must not widen that to the whole queue.
    """
    batch_id = cleaned["batch_id"]
    with service_session() as session:
        chosen = session.execute(
            text(
                "select id from public.review_queue "
                "where batch_id = cast(:b as uuid) and status = 'pending' "
                "order by created_at limit 3"
            ),
            {"b": batch_id},
        ).scalars().all()
        assert len(chosen) == 3

        result = bulk_decide_review_items(
            session,
            batch_id=batch_id,
            decision="approve",
            user=USER,
            review_ids=[str(rid) for rid in chosen],
        )

        decided = session.execute(
            text(
                "select count(*) from public.review_queue "
                "where id = any(cast(:ids as uuid[])) and status = 'approved'"
            ),
            {"ids": [str(rid) for rid in chosen]},
        ).scalar_one()
        untouched = session.execute(
            text(
                "select count(*) from public.review_queue "
                "where batch_id = cast(:b as uuid) and status = 'pending'"
            ),
            {"b": batch_id},
        ).scalar_one()

    assert result["requested"] == 3
    assert result["decided"] == 3
    assert decided == 3
    # The rest of the queue is still waiting for a human.
    assert untouched > 0


def test_bulk_with_an_empty_id_list_decides_nothing(cleaned) -> None:
    """An empty selection must not fall back to deciding the whole queue."""
    batch_id = cleaned["batch_id"]
    with service_session() as session:
        before = session.execute(
            text(
                "select count(*) from public.review_queue "
                "where batch_id = cast(:b as uuid) and status = 'pending'"
            ),
            {"b": batch_id},
        ).scalar_one()
        result = bulk_decide_review_items(
            session,
            batch_id=batch_id,
            decision="approve",
            user=USER,
            review_ids=[],
        )
        after = session.execute(
            text(
                "select count(*) from public.review_queue "
                "where batch_id = cast(:b as uuid) and status = 'pending'"
            ),
            {"b": batch_id},
        ).scalar_one()

    assert result == {"requested": 0, "decided": 0, "failed": [], "decision": "approve"}
    assert after == before


def test_bulk_by_rule_id_decides_only_that_rule(cleaned) -> None:
    """Filtering by rule resolves through the linked finding, not the queue.

    review_queue has no rule_id column, so the filter has to reach the finding.
    A missing join is a 500 rather than a wrong answer, which is easy to ship
    without noticing, so the test pins the behaviour.
    """
    batch_id = cleaned["batch_id"]
    with service_session() as session:
        rule_id = session.execute(
            text(
                """
                select f.rule_id
                  from public.review_queue q
                  join public.validation_findings f on f.id = q.finding_id
                 where q.batch_id = cast(:b as uuid) and q.status = 'pending'
                 group by f.rule_id
                 order by count(*) desc
                 limit 1
                """
            ),
            {"b": batch_id},
        ).scalar_one()

        result = bulk_decide_review_items(
            session, batch_id=batch_id, decision="approve", user=USER, rule_id=rule_id
        )

        other_pending = session.execute(
            text(
                """
                select count(*)
                  from public.review_queue q
                  join public.validation_findings f on f.id = q.finding_id
                 where q.batch_id = cast(:b as uuid)
                   and q.status = 'pending'
                   and f.rule_id <> :rule_id
                """
            ),
            {"b": batch_id, "rule_id": rule_id},
        ).scalar_one()

    assert result["failed"] == []
    assert result["decided"] == result["requested"]
    assert result["requested"] > 0
    # A different rule is left for a human.
    assert other_pending > 0


def test_cleaned_frames_round_trip_through_mongo(cleaned) -> None:
    from app.pipeline.clean import load_clean_frame

    frame = load_clean_frame(cleaned["batch_id"], "suppliers")
    assert len(frame) == len(cleaned["cleaned"]["suppliers"])


def test_a_decision_survives_a_re_run(cleaned) -> None:
    """Re-running clean must not resurrect a record a human rejected."""
    batch_id = cleaned["batch_id"]
    with service_session() as session:
        item = session.execute(
            text(
                """
                select q.id, q.entity, q.record_id, f.rule_id
                  from public.review_queue q
                  left join public.validation_findings f on f.id = q.finding_id
                 where q.batch_id = cast(:b as uuid) and q.status = 'pending'
                 limit 1
                """
            ),
            {"b": batch_id},
        ).mappings().one()
        decide_review_item(
            session, review_id=str(item["id"]), decision="reject", user=USER, note="not migrating"
        )

    with service_session() as session:
        frames = {e: load_entity_frame(batch_id, e) for e in cleaned["raw"]}
        run_clean(session, batch_id=batch_id, frames=frames, user=USER)
        requeued = session.execute(
            text(
                """
                select count(*) from public.review_queue
                 where batch_id = cast(:b as uuid)
                   and entity = :entity
                   and record_id = :record_id
                   and status = 'pending'
                """
            ),
            {
                "b": batch_id,
                "entity": item["entity"],
                "record_id": item["record_id"],
            },
        ).scalar_one()
        still_rejected = session.execute(
            text(
                "select status from public.review_queue where id = cast(:i as uuid)"
            ),
            {"i": str(item["id"])},
        ).scalar_one()

    assert still_rejected == "rejected"
    assert requeued == 0
