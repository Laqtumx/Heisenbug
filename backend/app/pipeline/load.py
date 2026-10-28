"""Stages 6-7 — DRY RUN, LOAD and ROLLBACK, plus reconciliation.

Dry run and load are the same code path pointed at different schemas. The dry
run writes into ``staging`` and the real load writes into ``public``; both call
``_load_into``. That is deliberate. A dry run that used different logic from the
real load would prove nothing, because the thing worth proving is that the load
itself is correct.

Every load is scoped to one batch. Loading the same batch twice replaces its
rows rather than duplicating them, and rollback is the same delete. That is what
makes the whole pipeline re-runnable during a demo without leaving debris.

Rows load in dependency order — categories before items, suppliers before
purchase orders — so a foreign key is never asked to reference a row that has
not arrived yet.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import mongo
from app.pipeline.entities import ENTITIES, EntitySpec
from app.pipeline.ingest import finish_stage, start_stage
from app.pipeline.target import (
    MONEY_COLUMNS,
    COUNT_COLUMNS,
    delete_batch_rows,
    fetch_rows,
    frame_to_rows,
    frame_totals,
    insert_rows,
    row_checksum,
    row_totals,
    table_columns,
    verify_row_counts,
)
from app.security import audit

logger = logging.getLogger(__name__)

STAGING_SCHEMA = "staging"
TARGET_SCHEMA = "public"


@dataclass
class EntityReconciliation:
    entity: str
    rows_in: int = 0
    rows_out: int = 0
    rows_expected: int = 0
    checksum: str = ""
    totals_in: dict[str, float] = field(default_factory=dict)
    totals_out: dict[str, float] = field(default_factory=dict)

    @property
    def counts_match(self) -> bool:
        return self.rows_out == self.rows_expected

    @property
    def totals_match(self) -> bool:
        keys = set(self.totals_in) | set(self.totals_out)
        # Money must match to the cent. Floating point accumulation over tens of
        # thousands of rows can drift in the last place or two, so a cent is the
        # tolerance rather than exact equality.
        return all(
            abs(self.totals_in.get(k, 0.0) - self.totals_out.get(k, 0.0)) < 0.01
            for k in keys
        )

    @property
    def balanced(self) -> bool:
        return self.counts_match and self.totals_match

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity": self.entity,
            "rows_in": self.rows_in,
            "rows_out": self.rows_out,
            "rows_expected": self.rows_expected,
            "checksum": self.checksum,
            "totals_in": self.totals_in,
            "totals_out": self.totals_out,
            "counts_match": self.counts_match,
            "totals_match": self.totals_match,
            "balanced": self.balanced,
        }


@dataclass
class ReconciliationReport:
    batch_id: str
    phase: str
    entities: dict[str, EntityReconciliation] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    held_back: dict[str, int] = field(default_factory=dict)
    duration_ms: int = 0
    health_score: float | None = None

    @property
    def balanced(self) -> bool:
        return all(r.balanced for r in self.entities.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "phase": self.phase,
            "balanced": self.balanced,
            "counts": self.counts,
            "held_back": self.held_back,
            "duration_ms": self.duration_ms,
            "health_score": self.health_score,
            "per_entity": {name: r.to_dict() for name, r in self.entities.items()},
        }


# ------------------------------------------------------------------- load ---


def held_back_records(session: Session, batch_id: str) -> dict[str, set[str]]:
    """Records per entity that must not be loaded.

    Two kinds of record are excluded. A record with an unresolved blocker has a
    known defect that would make it wrong in the new ERP — a missing mandatory
    field, an orphaned foreign key, an unbalanced ledger line. A record a
    reviewer rejected is a human saying "do not migrate this". Both stay behind,
    and the reconciliation report counts them separately.

    A record a reviewer *approved* is loadable, so 'approved' does not block.
    """
    rows = session.execute(
        text(
            """
            select entity, record_id,
                   bool_or(status = 'rejected') as rejected
              from public.validation_findings
             where batch_id = cast(:b as uuid)
               and (
                 status = 'rejected'
                 or (severity = 'blocker' and status in ('open', 'pending_review'))
               )
             group by entity, record_id
            """
        ),
        {"b": batch_id},
    ).all()
    held: dict[str, set[str]] = {}
    for entity, record_id, _rejected in rows:
        held.setdefault(str(entity), set()).add(str(record_id))
    return held


def loadable_frames(
    session: Session, batch_id: str, frames: dict[str, pd.DataFrame]
) -> tuple[dict[str, pd.DataFrame], dict[str, int]]:
    """Split the cleaned frames into what can load and what must wait.

    Returns the loadable frames and, per entity, how many records were held
    back. The held-back records are still in the cleaned set in Mongo and still
    visible in the review queue; they simply do not travel yet.
    """
    held = held_back_records(session, batch_id)
    loadable: dict[str, pd.DataFrame] = {}
    held_counts: dict[str, int] = {}

    for entity, frame in frames.items():
        blocked = held.get(entity)
        if not blocked or "record_id" not in frame.columns:
            loadable[entity] = frame
            continue
        # Counted from the frame, not from the findings. The cleaner archives and
        # merges records away, and a finding can outlive the record it named; a
        # count taken from the findings would exceed the records that exist and
        # the reconciliation would not add up.
        keep = ~frame["record_id"].astype(str).isin(blocked)
        loadable[entity] = frame.loc[keep].reset_index(drop=True)
        held_counts[entity] = int((~keep).sum())

    return loadable, held_counts





def _load_into(
    session: Session,
    *,
    batch_id: str,
    frames: dict[str, pd.DataFrame],
    schema: str,
) -> tuple[dict[str, int], dict[str, str]]:
    """Write every entity into one schema, in dependency order.

    Returns per-entity row counts and checksums. A target table that does not
    exist is skipped with a warning rather than aborting the run: the staging
    schema is built from the target tables, so a missing staging table means a
    migration has not run, not that the data is wrong.
    """
    counts: dict[str, int] = {}
    checksums: dict[str, str] = {}

    for spec in sorted(ENTITIES, key=lambda s: s.load_order):
        frame = frames.get(spec.name)
        if frame is None:
            continue

        available = table_columns(session, schema, spec.target_table)
        rows = frame_to_rows(spec, frame, batch_id, available)
        delete_batch_rows(session, schema, spec, batch_id)
        written = insert_rows(session, schema, spec, rows)
        counts[spec.name] = written
        checksums[spec.name] = row_checksum(rows, spec)

    return counts, checksums


def run_dry_run(
    session: Session,
    *,
    batch_id: str,
    frames: dict[str, pd.DataFrame],
    user=None,
    request_id: str | None = None,
) -> ReconciliationReport:
    """Simulate the load into the staging schema and reconcile it.

    The staging schema is a structural copy of the target, so a dry run catches
    the failures a load would hit — a value too wide for a column, a duplicate
    key, a broken foreign key — without touching the live tables.
    """
    started = time.perf_counter()
    start_stage(session, batch_id, "dry_run")

    report = ReconciliationReport(batch_id=batch_id, phase="dry_run")

    # Records with an unresolved blocker stay behind. The dry run then proves
    # that everything which *is* loadable actually loads, which is the useful
    # question.
    loadable, held = loadable_frames(session, batch_id, frames)
    report.held_back = held

    counts, checksums = _load_into(
        session, batch_id=batch_id, frames=loadable, schema=STAGING_SCHEMA
    )

    for spec in sorted(ENTITIES, key=lambda s: s.load_order):
        frame = loadable.get(spec.name)
        if frame is None:
            continue
        recon = EntityReconciliation(
            entity=spec.name,
            rows_in=len(frame),
            rows_expected=len(frame),
            rows_out=verify_row_counts(session, STAGING_SCHEMA, spec, batch_id, len(frame)),
            checksum=checksums.get(spec.name, ""),
            totals_in=frame_totals(spec, frame),
        )
        # Totals are read back from the staging table, not recomputed from the
        # frame. Reading them back is what proves the values survived the round
        # trip through the database types.
        staged = fetch_rows(session, STAGING_SCHEMA, spec, batch_id)
        recon.totals_out = row_totals(spec, staged)
        report.entities[spec.name] = recon

    report.counts = _reconciliation_counts(session, batch_id, frames, counts, held)
    report.health_score = _health_score(session, batch_id)
    report.duration_ms = int((time.perf_counter() - started) * 1000)

    _persist_report(session, report)
    session.execute(
        text(
            """
            update public.migration_batches
               set status = 'dry_run_complete'
             where id = cast(:b as uuid)
            """
        ),
        {"b": batch_id},
    )
    finish_stage(session, batch_id, "dry_run", summary=report.to_dict())
    audit.record(
        session,
        action="dry_run.completed",
        user=user,
        entity="migration_batches",
        entity_id=batch_id,
        batch_id=batch_id,
        after={
            "balanced": report.balanced,
            "counts": report.counts,
            "unbalanced_entities": [
                name for name, r in report.entities.items() if not r.balanced
            ],
        },
        request_id=request_id,
    )
    return report


def run_load(
    session: Session,
    *,
    batch_id: str,
    frames: dict[str, pd.DataFrame],
    user=None,
    request_id: str | None = None,
    require_balanced_dry_run: bool = True,
) -> ReconciliationReport:
    """Load the batch into the target ERP tables.

    The load is transactional: the session commits once, at the end, so a failure
    half way through leaves the target tables exactly as they were. A partial
    migration is worse than no migration, because nothing downstream can tell
    which half arrived.

    A batch whose dry run did not reconcile is refused unless the caller
    explicitly overrides. Loading data that does not add up is the mistake this
    platform exists to prevent.
    """
    if require_balanced_dry_run:
        _assert_dry_run_balanced(session, batch_id)

    started = time.perf_counter()
    start_stage(session, batch_id, "load")

    report = ReconciliationReport(batch_id=batch_id, phase="load")
    loadable, held = loadable_frames(session, batch_id, frames)
    report.held_back = held
    counts, checksums = _load_into(
        session, batch_id=batch_id, frames=loadable, schema=TARGET_SCHEMA
    )

    for spec in sorted(ENTITIES, key=lambda s: s.load_order):
        frame = loadable.get(spec.name)
        if frame is None:
            continue
        recon = EntityReconciliation(
            entity=spec.name,
            rows_in=len(frame),
            rows_expected=len(frame),
            rows_out=verify_row_counts(session, TARGET_SCHEMA, spec, batch_id, len(frame)),
            checksum=checksums.get(spec.name, ""),
            totals_in=frame_totals(spec, frame),
        )
        loaded = fetch_rows(session, TARGET_SCHEMA, spec, batch_id)
        recon.totals_out = row_totals(spec, loaded)
        report.entities[spec.name] = recon

    report.counts = _reconciliation_counts(session, batch_id, frames, counts, held)
    report.health_score = _health_score(session, batch_id)
    report.duration_ms = int((time.perf_counter() - started) * 1000)

    _persist_report(session, report)
    session.execute(
        text(
            """
            update public.migration_batches
               set status = 'loaded',
                   record_count_loaded = :out,
                   record_count_rejected = :rejected,
                   completed_at = now()
             where id = cast(:b as uuid)
            """
        ),
        {
            "b": batch_id,
            "out": sum(counts.values()),
            "rejected": report.counts.get("records_rejected", 0),
        },
    )
    finish_stage(session, batch_id, "load", summary=report.to_dict())
    audit.record(
        session,
        action="load.completed",
        user=user,
        entity="migration_batches",
        entity_id=batch_id,
        batch_id=batch_id,
        after={
            "balanced": report.balanced,
            "rows_loaded": sum(counts.values()),
            "counts": report.counts,
        },
        request_id=request_id,
    )
    return report


def _assert_dry_run_balanced(session: Session, batch_id: str) -> None:
    row = session.execute(
        text(
            """
            select balanced from public.reconciliation_reports
             where batch_id = cast(:b as uuid) and phase = 'dry_run'
             order by created_at desc
             limit 1
            """
        ),
        {"b": batch_id},
    ).scalar()
    if row is None:
        raise ValueError(
            "no dry run found for this batch; run the dry run before loading"
        )
    if not row:
        raise ValueError(
            "the dry run did not reconcile; refusing to load data that does not "
            "add up. Review the reconciliation report or load with "
            "require_balanced_dry_run=false to override."
        )


def rollback_batch(
    session: Session,
    *,
    batch_id: str,
    user=None,
    request_id: str | None = None,
    include_staging: bool = True,
) -> dict[str, int]:
    """Remove every row this batch loaded, in reverse dependency order.

    Children go first. Deleting a category before the items that reference it
    would trip the foreign key, and the whole rollback would fail on the first
    entity.
    """
    start_stage(session, batch_id, "rollback")
    removed: dict[str, int] = {}

    for spec in sorted(ENTITIES, key=lambda s: s.load_order, reverse=True):
        for schema in (
            (TARGET_SCHEMA, STAGING_SCHEMA) if include_staging else (TARGET_SCHEMA,)
        ):
            try:
                removed[f"{schema}.{spec.target_table}"] = delete_batch_rows(
                    session, schema, spec, batch_id
                )
            except Exception:
                logger.warning(
                    "rollback could not clear %s.%s", schema, spec.target_table
                )

    total = sum(removed.values())
    session.execute(
        text(
            """
            update public.migration_batches
               set status = 'rolled_back'
             where id = cast(:b as uuid)
            """
        ),
        {"b": batch_id},
    )
    finish_stage(session, batch_id, "rollback", summary={"removed": removed})
    audit.record(
        session,
        action="rollback.completed",
        user=user,
        entity="migration_batches",
        entity_id=batch_id,
        batch_id=batch_id,
        after={"rows_removed": total, "per_table": removed},
        request_id=request_id,
    )
    return removed


# --------------------------------------------------------- reconciliation ---


def _reconciliation_counts(
    session: Session,
    batch_id: str,
    frames: dict[str, pd.DataFrame],
    loaded: dict[str, int],
    held: dict[str, int] | None = None,
) -> dict[str, int]:
    """The headline numbers for the reconciliation view.

    Every record that came in is accounted for in exactly one bucket: it was
    loaded, it was archived as stale, or it was rejected by a reviewer. If those
    three do not sum to the input, something was lost, and that is the single
    most important thing the report has to say.
    """
    # The denominator is the original ingest count, not the cleaned count. The
    # point of the report is that every record that came in is accounted for,
    # so archiving and merging have to appear in the arithmetic rather than
    # being quietly subtracted before it starts.
    rows_in = int(
        session.execute(
            text(
                "select record_count_in from public.migration_batches "
                "where id = cast(:b as uuid)"
            ),
            {"b": batch_id},
        ).scalar()
        or sum(len(f) for f in frames.values())
    )
    loaded_total = sum(loaded.values())

    held_total = sum(held.values()) if held else 0
    rejected = session.execute(
        text(
            """
            select count(*) from public.review_queue
             where batch_id = cast(:b as uuid) and status = 'rejected'
            """
        ),
        {"b": batch_id},
    ).scalar_one()
    merged = session.execute(
        text(
            """
            select count(*) from public.validation_findings
             where batch_id = cast(:b as uuid) and status = 'applied'
            """
        ),
        {"b": batch_id},
    ).scalar_one()
    archived = session.execute(
        text(
            "select record_count_archived from public.migration_batches "
            "where id = cast(:b as uuid)"
        ),
        {"b": batch_id},
    ).scalar_one()

    accounted = int(loaded_total + held_total + rejected + merged + int(archived or 0))
    return {
        "records_in": int(rows_in),
        "records_loaded": int(loaded_total),
        "records_held_back": int(held_total),
        "records_rejected": int(rejected),
        "records_archived": int(archived or 0),
        "records_merged": int(merged),
        "records_accounted_for": accounted,
        # True when nothing went missing between ingest and load. This is the
        # single most important line in the report, so it is computed here
        # rather than left for the reader to check by hand.
        "fully_accounted": accounted == int(rows_in),
    }


def _health_score(session: Session, batch_id: str) -> float | None:
    return session.execute(
        text(
            "select health_score_before from public.migration_batches "
            "where id = cast(:b as uuid)"
        ),
        {"b": batch_id},
    ).scalar()


def _persist_report(session: Session, report: ReconciliationReport) -> None:
    payload = report.to_dict()
    session.execute(
        text(
            """
            insert into public.reconciliation_reports (
                batch_id, phase, health_score, counts, financial_totals,
                checksums, per_entity, balanced
            ) values (
                cast(:b as uuid), :phase, :health, cast(:counts as jsonb),
                cast(:totals as jsonb), cast(:checksums as jsonb),
                cast(:per_entity as jsonb), :balanced
            )
            """
        ),
        {
            "b": report.batch_id,
            "phase": report.phase,
            "health": report.health_score,
            "counts": _json(report.counts),
            "totals": _json(
                {
                    name: {"in": r.totals_in, "out": r.totals_out}
                    for name, r in report.entities.items()
                }
            ),
            "checksums": _json({name: r.checksum for name, r in report.entities.items()}),
            "per_entity": _json(payload["per_entity"]),
            "balanced": report.balanced,
        },
    )


def latest_reconciliation(session: Session, batch_id: str, phase: str | None = None) -> dict:
    """The most recent reconciliation report for a batch, as a plain dict."""
    sql = (
        "select phase, health_score, counts, financial_totals, checksums, "
        "per_entity, balanced, created_at "
        "from public.reconciliation_reports where batch_id = cast(:b as uuid)"
    )
    params: dict[str, Any] = {"b": batch_id}
    if phase:
        sql += " and phase = :phase"
        params["phase"] = phase
    sql += " order by created_at desc limit 1"

    row = session.execute(text(sql), params).mappings().first()
    if row is None:
        raise LookupError(f"no reconciliation report for batch {batch_id}")
    result = dict(row)
    result["created_at"] = result["created_at"].isoformat()
    return result


def migration_health_score(session: Session, batch_id: str) -> dict[str, Any]:
    """The go-live readiness score, combining validation and reconciliation.

    Two things have to be true before a batch is ready: the data has to be clean
    enough to load, and what loaded has to match what was expected. A perfect
    validation score with an unbalanced load is not ready, so the load check
    gates the result.
    """
    batch = session.execute(
        text(
            """
            select health_score_before, health_score_after, status,
                   record_count_in, record_count_loaded,
                   record_count_archived, record_count_rejected
              from public.migration_batches
             where id = cast(:b as uuid)
            """
        ),
        {"b": batch_id},
    ).mappings().first()
    if batch is None:
        raise LookupError(f"batch {batch_id} not found")

    # Once the cleaner has scored the cleaned frames, that is the score the
    # batch actually carries into the new ERP. Falling back to the before score
    # would report the data as worse than it is and understate the work done.
    after = batch["health_score_after"]
    before = batch["health_score_before"]
    base = float(after if after is not None else (before if before is not None else 0.0))

    load_report = session.execute(
        text(
            """
            select balanced from public.reconciliation_reports
             where batch_id = cast(:b as uuid) and phase = 'load'
             order by created_at desc limit 1
            """
        ),
        {"b": batch_id},
    ).scalar()

    open_blockers = session.execute(
        text(
            """
            select count(*) from public.validation_findings
             where batch_id = cast(:b as uuid)
               and severity = 'blocker'
               and status in ('open', 'pending_review')
            """
        ),
        {"b": batch_id},
    ).scalar_one()

    checklist = [
        {
            "item": "Validation completed",
            "passed": batch["health_score_before"] is not None,
            "detail": "Findings were produced for this batch.",
        },
        {
            "item": "No unresolved blockers",
            "passed": int(open_blockers) == 0,
            "detail": f"{int(open_blockers)} blocker findings still open.",
        },
        {
            "item": "Dry run reconciled",
            "passed": _dry_run_balanced(session, batch_id),
            "detail": "Staged row counts and totals match the cleaned data.",
        },
        {
            "item": "Load reconciled",
            "passed": bool(load_report),
            "detail": "Target row counts and totals match the cleaned data.",
        },
        {
            "item": "Batch loaded",
            "passed": batch["status"] in {"loaded", "verified"},
            "detail": f"Batch status is {batch['status']}.",
        },
    ]

    return {
        "batch_id": batch_id,
        "health_score": base,
        "status": batch["status"],
        "records_in": int(batch["record_count_in"] or 0),
        "records_loaded": int(batch["record_count_loaded"] or 0),
        "records_archived": int(batch["record_count_archived"] or 0),
        "open_blockers": int(open_blockers),
        "checklist": checklist,
        "go_live_ready": all(c["passed"] for c in checklist),
    }


def _dry_run_balanced(session: Session, batch_id: str) -> bool:
    value = session.execute(
        text(
            """
            select balanced from public.reconciliation_reports
             where batch_id = cast(:b as uuid) and phase = 'dry_run'
             order by created_at desc limit 1
            """
        ),
        {"b": batch_id},
    ).scalar()
    return bool(value)


def _json(value: Any) -> str:
    import json

    return json.dumps(value, default=str)
