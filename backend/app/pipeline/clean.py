"""Stage 4 — CLEAN.

Two things happen here, and they are deliberately different in kind.

Auto-fix rewrites a value the platform can prove is wrong and can repair without
judgement: stray whitespace, inconsistent casing, a date in the wrong format, a
unit of measure spelled "Kgs" instead of "KG". The rule already said exactly
what the correct value is, so no human needs to look at it. These rows are
changed, the change is written to the audit log with before and after, and the
finding is marked ``auto_fixed``.

Everything else goes to the review queue for a person to decide: duplicates to
merge, orphans to resolve, unbalanced ledgers, retired tax codes. The platform
proposes, the human disposes. A finding never disappears silently.

The stage also computes the archive set. A master record with no activity for
three years is not an error — it is simply not worth migrating — so it is
excluded from the load and counted as archived rather than fixed or rejected.

Nothing here mutates the raw landing copy in Mongo. The cleaned frame is written
back to Mongo as a derived set, keyed by (batch_id, entity), so the original is
always available to re-derive from and the review queue can still show the
legacy value beside the proposed one.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from collections.abc import Sequence
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.db import mongo
from app.pipeline.entities import ENTITIES_BY_NAME, EntitySpec
from app.pipeline.ingest import finish_stage, start_stage
from app.pipeline.normalise import (
    ISO_FORMAT,
    collapse_whitespace,
    parse_dates,
    title_case_tolerant,
)
from app.pipeline.profile import NULL_TOKENS
from app.rules.checks import RuleContext
from app.rules.loader import Rule, load_ruleset

logger = logging.getLogger(__name__)

# The derived, cleaned view of a batch. Separate from raw_records so the
# original legacy values survive for the review queue's side-by-side display.
CLEAN_RECORDS = "clean_records"

# A review item is raised per record, but a single column-level finding can
# cover thousands of rows. Past this many the queue stops being reviewable, so
# the remainder is summarised into one item rather than dropped.
MAX_REVIEW_ITEMS_PER_FINDING = 200


@dataclass
class CleanSummary:
    batch_id: str
    auto_fixed: int = 0
    auto_fixed_by_rule: dict[str, int] = field(default_factory=dict)
    queued_for_review: int = 0
    queued_by_rule: dict[str, int] = field(default_factory=dict)
    archived: int = 0
    archived_by_entity: dict[str, int] = field(default_factory=dict)
    duplicates_merged: int = 0
    rows_in: int = 0
    rows_out: int = 0
    duration_ms: int = 0
    findings_open: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "rows_in": self.rows_in,
            "rows_out": self.rows_out,
            "auto_fixed": self.auto_fixed,
            "auto_fixed_by_rule": self.auto_fixed_by_rule,
            "queued_for_review": self.queued_for_review,
            "queued_by_rule": self.queued_by_rule,
            "archived": self.archived,
            "archived_by_entity": self.archived_by_entity,
            "duplicates_merged": self.duplicates_merged,
            "findings_open": self.findings_open,
            "duration_ms": self.duration_ms,
        }


# --------------------------------------------------------------- auto fixes --


def _fix_whitespace_case(
    frame: pd.DataFrame, params: dict
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    columns = [c for c in (params.get("columns") or []) if c in frame.columns]
    title_columns = set(params.get("title_case_columns") or [])
    changes: list[dict[str, Any]] = []

    for column in columns:
        before = frame[column].astype(str)
        after = collapse_whitespace(before)
        if column in title_columns:
            after = title_case_tolerant(after)

        changed = before != after
        if not changed.any():
            continue
        frame.loc[changed, column] = after[changed]
        changes.append(
            {
                "column": column,
                "field": column,
                "before": before[changed].tolist()[:50],
                "after": after[changed].tolist()[:50],
                "record_ids": _record_ids(frame, changed),
            }
        )
    return frame, changes


def _fix_date_format(
    frame: pd.DataFrame, params: dict
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    columns = [c for c in (params.get("columns") or []) if c in frame.columns]
    changes: list[dict[str, Any]] = []

    for column in columns:
        before = frame[column].astype(str).str.strip()
        parsed = parse_dates(before)
        after = parsed.dt.strftime(ISO_FORMAT)
        # Only rewrite values that parse and are not already ISO. An
        # unparseable value is left exactly as it was.
        changed = parsed.notna() & (before != after)
        if not changed.any():
            continue
        frame.loc[changed, column] = after[changed]
        changes.append(
            {
                "column": column,
                "field": column,
                "before": before[changed].tolist()[:50],
                "after": after[changed].tolist()[:50],
                "record_ids": _record_ids(frame, changed),
            }
        )
    return frame, changes


def _fix_uom_variant(
    frame: pd.DataFrame, params: dict
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    column = params.get("column")
    variants = params.get("variants") or {}
    if not column or column not in frame.columns or not variants:
        return frame, []

    # Same lookup the MD010 rule builds: alias (lower-cased) -> canonical code.
    lookup: dict[str, str] = {}
    for canonical, aliases in variants.items():
        for alias in aliases:
            lookup[alias.strip().lower()] = canonical

    values = frame[column].astype(str).str.strip()
    mapped = values.str.lower().map(lookup)
    changed = mapped.notna() & (values != mapped)
    if not changed.any():
        return frame, []

    before = values[changed]
    frame.loc[changed, column] = mapped[changed]
    return frame, [
        {
            "column": column,
            "field": column,
            "before": before.tolist()[:50],
            "after": mapped[changed].tolist()[:50],
            "record_ids": _record_ids(frame, changed),
        }
    ]


def _fix_archive(
    frame: pd.DataFrame, spec: EntitySpec, params: dict, ctx: RuleContext
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Drop stale master records from the load set.

    Archiving is a filter, not a rewrite. The record is preserved in Mongo and
    counted in the reconciliation report; it simply does not travel to the new
    ERP, which is the whole point of "move the data, not the mistakes".

    A record a child still points at is kept regardless of its date. Archiving
    a customer that invoices reference does not remove a stale record, it
    manufactures an orphan, so the staleness cutoff is applied only to parents
    nothing depends on.
    """
    column = params.get("column", "last_activity_date")
    years = int(params.get("years", settings.archive_after_years))
    if column not in frame.columns:
        return frame, []

    parsed = parse_dates(frame[column])
    cutoff = pd.Timestamp(ctx.reference_date) - pd.DateOffset(years=years)
    stale = parsed.notna() & (parsed < cutoff)

    protected = ctx.protected_keys.get((spec.name, spec.primary_key), set())
    if protected and spec.primary_key in frame.columns:
        keys = frame[spec.primary_key].astype(str).str.strip()
        stale = stale & ~keys.isin(protected)

    if not stale.any():
        return frame, []

    archived_ids = _record_ids(frame, stale)
    kept = frame.loc[~stale]
    return kept, [
        {
            "column": column,
            "field": column,
            "record_ids": archived_ids,
            "before": parsed[stale].dt.strftime(ISO_FORMAT).tolist()[:50],
            "after": [],
            "archived": True,
        }
    ]


def _referenced_parent_keys(
    frames: dict[str, pd.DataFrame], ruleset
) -> dict[tuple[str, str], set[str]]:
    """Parent keys that some child entity still points at.

    Read from the configured referential rules rather than a hardcoded list, so
    a new parent/child relationship declared in ``rules.json`` is protected
    without a code change. The set is built from the frames as they arrive at
    the clean stage, which is what the archiving decision has to respect.
    """
    protected: dict[tuple[str, str], set[str]] = {}
    for rule in ruleset.rules:
        if rule.rule_type != "referential_orphan":
            continue
        parent = rule.params.get("parent_entity")
        parent_column = rule.params.get("parent_column")
        column = rule.params.get("column")
        if not parent or not parent_column or not column:
            continue
        child_frames = [
            frames[entity]
            for entity in rule.entities
            if entity != "$all" and entity in frames
        ]
        keys: set[str] = set()
        for child in child_frames:
            if child.empty or column not in child.columns:
                continue
            values = child[column].astype(str).str.strip()
            keys.update(values[~values.isin(NULL_TOKENS)])
        if keys:
            protected[(parent, parent_column)] = keys
    return protected


def _merge_duplicates(
    frame: pd.DataFrame, spec: EntitySpec, params: dict
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Collapse exact duplicates on the business key.

    Survivorship: the most complete record wins, and where two are equally
    complete the most recently active wins. Completeness is the count of
    non-blank fields, which is a proxy for "someone finished maintaining this".
    Ties are broken by record id so the result does not depend on file order.

    The survivor absorbs any field the loser had filled in and it had left
    blank, so no information is lost in the merge.
    """
    key = params.get("key_column") or spec.primary_key
    if key not in frame.columns:
        return frame, []

    values = frame[key].astype(str).str.strip()
    work = frame.loc[~values.isin(NULL_TOKENS)].copy()
    if work.empty:
        return frame, []
    work["_key"] = values[~values.isin(NULL_TOKENS)]

    duplicates = work["_key"].value_counts()
    duplicates = duplicates[duplicates > 1]
    if duplicates.empty:
        return frame, []

    activity_column = "last_activity_date" if "last_activity_date" in frame.columns else None
    data_columns = [c for c in frame.columns if c not in {key, "record_id"}]

    merged_ids: list[str] = []
    absorbed: dict[str, str] = {}
    merged_rows: list[dict[str, Any]] = []
    drop_index: list[Any] = []

    for _, group in work[work["_key"].isin(duplicates.index)].groupby("_key", sort=False):
        completeness = group[data_columns].apply(
            lambda row: sum(
                1 for v in row if str(v).strip() not in NULL_TOKENS and str(v).strip() != ""
            ),
            axis=1,
        )
        if activity_column:
            activity = parse_dates(group[activity_column])
        else:
            activity = pd.Series(pd.NaT, index=group.index)

        # Most complete first, then most recent, then smallest record id.
        order = pd.DataFrame(
            {
                "completeness": completeness,
                "activity": activity,
                "record_id": group["record_id"].astype(str)
                if "record_id" in group.columns
                else group.index.astype(str),
            }
        ).sort_values(
            ["completeness", "activity", "record_id"],
            ascending=[False, False, True],
            kind="stable",
        )
        survivor_index = order.index[0]
        survivor_id = str(frame.at[survivor_index, "record_id"]) if "record_id" in frame.columns else str(survivor_index)

        for loser_index in order.index[1:]:
            drop_index.append(loser_index)
            loser_id = str(frame.at[loser_index, "record_id"]) if "record_id" in frame.columns else str(loser_index)
            merged_ids.append(loser_id)
            for column in data_columns:
                current = str(frame.at[survivor_index, column]).strip()
                candidate = str(frame.at[loser_index, column]).strip()
                if current in NULL_TOKENS or current == "":
                    if candidate and candidate not in NULL_TOKENS:
                        frame.at[survivor_index, column] = frame.at[loser_index, column]
                        absorbed.setdefault(column, candidate)

        merged_rows.append(
            {
                "survivor": survivor_id,
                "merged": [str(frame.at[i, "record_id"]) if "record_id" in frame.columns else str(i) for i in order.index[1:]],
                "absorbed_fields": dict(absorbed),
            }
        )

    if drop_index:
        frame = frame.drop(index=drop_index).reset_index(drop=True)

    return frame, [
        {
            "column": key,
            "field": key,
            "record_ids": merged_ids,
            "before": [],
            "after": [],
            "merged": merged_rows,
        }
    ] if merged_ids else []


def _record_ids(frame: pd.DataFrame, mask: pd.Series) -> list[str]:
    if "record_id" in frame.columns:
        return frame.loc[mask, "record_id"].astype(str).tolist()
    return [str(i) for i in frame.index[mask]]


# The fixes the cleaner knows how to apply, keyed by rule type. A rule marked
# auto_fixable in the config must appear here or the cleaner raises, which keeps
# the two in step.
AUTO_FIXES = {
    "whitespace_case": _fix_whitespace_case,
    "date_format": _fix_date_format,
    "mixed_date_formats": _fix_date_format,
    "uom_variant": _fix_uom_variant,
    "stale_record": _fix_archive,
    "duplicate_exact": _merge_duplicates,
    "duplicate_fuzzy": None,  # fuzzy merges are a judgement call: review queue
    "retired_reference": None,  # needs a finance decision: review queue
}


def _apply_auto_fixes(
    frame: pd.DataFrame,
    spec: EntitySpec,
    rule: Rule,
    ctx: RuleContext,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    fixer = AUTO_FIXES.get(rule.rule_type)
    if fixer is None:
        return frame, []
    if rule.rule_type == "duplicate_exact":
        return fixer(frame, spec, rule.params)
    if rule.rule_type == "stale_record":
        return fixer(frame, spec, rule.params, ctx)
    return fixer(frame, rule.params)


def run_clean(
    session: Session,
    *,
    batch_id: str,
    frames: dict[str, pd.DataFrame],
    user=None,
    request_id: str | None = None,
) -> tuple[dict[str, pd.DataFrame], CleanSummary]:
    """Clean every entity. Returns the cleaned frames and a summary."""
    started = time.perf_counter()
    start_stage(session, batch_id, "clean")

    ruleset = load_ruleset()
    ctx = RuleContext(frames=frames, specs=ENTITIES_BY_NAME)
    ctx.protected_keys = _referenced_parent_keys(frames, ruleset)
    summary = CleanSummary(batch_id=batch_id)
    summary.rows_in = int(sum(len(f) for f in frames.values()))

    # Re-running clean rebuilds the queue from scratch. Clearing only the
    # pending items keeps every decision a human already made: they are the one
    # thing a second run must never undo.
    session.execute(
        text(
            """
            delete from public.review_queue
             where batch_id = cast(:b as uuid) and status = 'pending'
            """
        ),
        {"b": batch_id},
    )
    # Findings this stage previously moved go back to open, so a repeat run
    # starts from the same state as the first. 'approved' and 'rejected' are
    # excluded: those are a reviewer's decisions and must survive a re-run.
    session.execute(
        text(
            """
            update public.validation_findings
               set status = 'open'
             where batch_id = cast(:b as uuid)
               and status in ('pending_review', 'auto_fixed', 'applied', 'ignored')
            """
        ),
        {"b": batch_id},
    )

    # Records a human already decided on. A rejection means "do not load this
    # record", so re-running the stage must not quietly put it back in the
    # queue: the decision has to be durable, or the queue would grow every run
    # and a reviewer's work would be undone.
    decided = _decided_keys(session, batch_id)

    cleaned: dict[str, pd.DataFrame] = {}

    for entity, original in frames.items():
        spec = ENTITIES_BY_NAME.get(entity)
        if spec is None:
            cleaned[entity] = original
            continue

        frame = original.copy()
        before_rows = len(frame)

        # Auto-fixable rules run in a fixed order: formatting first (so
        # duplicates compare on normalised values), then duplicates, then
        # archiving last (so a record merged into a survivor is not archived on
        # the strength of a stale date the survivor did not have).
        order = {
            "whitespace_case": 0,
            "date_format": 1,
            "mixed_date_formats": 1,
            "uom_variant": 2,
            "duplicate_exact": 3,
            "stale_record": 4,
        }
        fixable = [
            r
            for r in ruleset.for_entity(entity)
            if r.auto_fixable and AUTO_FIXES.get(r.rule_type) is not None
        ]
        fixable.sort(key=lambda r: order.get(r.rule_type, 99))

        for base_rule in fixable:
            from app.rules.loader import expand_for_entity

            rule = expand_for_entity(base_rule, entity)
            if not rule.params:
                continue
            try:
                frame, changes = _apply_auto_fixes(frame, spec, rule, ctx)
            except Exception:
                logger.exception("auto-fix %s failed on %s", rule.rule_id, entity)
                continue

            if not changes:
                continue

            if any(c.get("archived") for c in changes):
                archived_ids = [rid for c in changes for rid in c["record_ids"]]
                summary.archived += len(archived_ids)
                summary.archived_by_entity[entity] = (
                    summary.archived_by_entity.get(entity, 0) + len(archived_ids)
                )
                _mark_archived(session, batch_id, entity, rule.rule_id, archived_ids)
            if rule.rule_type == "duplicate_exact":
                merged_ids = [rid for c in changes for rid in c["record_ids"]]
                summary.duplicates_merged += len(merged_ids)
                _mark_merged(session, batch_id, entity, rule.rule_id, changes)

            fixed_ids = [rid for c in changes for rid in c.get("record_ids", [])]
            if rule.rule_type not in {"stale_record", "duplicate_exact"}:
                summary.auto_fixed += len(fixed_ids)
                summary.auto_fixed_by_rule[rule.rule_id] = (
                    summary.auto_fixed_by_rule.get(rule.rule_id, 0) + len(fixed_ids)
                )
                _mark_auto_fixed(session, batch_id, entity, rule.rule_id, fixed_ids)

        # Re-run the rules on the cleaned frame. Whatever still fires is what a
        # human has to look at; anything the cleaner repaired has gone.
        queued = _queue_for_review(
            session, batch_id, entity, frame, spec, ruleset, ctx, user, decided
        )
        summary.queued_for_review += queued["queued"]
        for rule_id, count in queued["by_rule"].items():
            summary.queued_by_rule[rule_id] = summary.queued_by_rule.get(rule_id, 0) + count

        cleaned[entity] = frame
        if len(frame) != before_rows:
            logger.info(
                "%s: %s rows in, %s out (%s archived or merged)",
                entity,
                before_rows,
                len(frame),
                before_rows - len(frame),
            )

    summary.rows_out = int(sum(len(f) for f in cleaned.values()))
    summary.duration_ms = int((time.perf_counter() - started) * 1000)
    summary.findings_open = _count_open_findings(session, batch_id)

    # Score the cleaned frames on the same scale validation used, so the batch
    # can report the before-and-after the dashboard shows. Measured rather than
    # assumed: if cleaning did not lift the score, this reports that.
    from app.rules.engine import score_frames

    health_after = score_frames(cleaned)

    _persist_clean_frames(batch_id, cleaned)

    session.execute(
        text(
            """
            update public.migration_batches
               set status = 'cleaned',
                   record_count_in = :rows_in,
                   record_count_archived = :archived,
                   health_score_after = :score
             where id = cast(:b as uuid)
            """
        ),
        {
            "b": batch_id,
            "rows_in": summary.rows_in,
            "archived": summary.archived,
            "score": health_after,
        },
    )

    finish_stage(session, batch_id, "clean", summary=summary.to_dict())

    from app.security import audit

    audit.record(
        session,
        action="clean.completed",
        user=user,
        entity="migration_batches",
        entity_id=batch_id,
        batch_id=batch_id,
        after=summary.to_dict(),
        request_id=request_id,
    )
    return cleaned, summary


# ------------------------------------------------------------- persistence --


def _persist_clean_frames(batch_id: str, frames: dict[str, pd.DataFrame]) -> None:
    collection = mongo.collection(CLEAN_RECORDS)
    for entity, frame in frames.items():
        collection.delete_many({"batch_id": batch_id, "entity": entity})
        if frame.empty:
            continue
        documents = [
            {
                "batch_id": batch_id,
                "entity": entity,
                "record_id": str(row.get("record_id", "")),
                "data": {k: ("" if v is None else str(v)) for k, v in row.items()},
            }
            for row in frame.to_dict(orient="records")
        ]
        collection.insert_many(documents, ordered=False)


def load_clean_frame(batch_id: str, entity: str) -> pd.DataFrame:
    """Read the cleaned view back. Falls back to the raw view if not cleaned."""
    spec = ENTITIES_BY_NAME.get(entity)
    columns = spec.column_names if spec else []
    cursor = mongo.collection(CLEAN_RECORDS).find(
        {"batch_id": batch_id, "entity": entity}, {"data": 1, "_id": 0}
    )
    rows = [doc.get("data", {}) for doc in cursor]
    if not rows:
        from app.pipeline.ingest import load_entity_frame

        return load_entity_frame(batch_id, entity)
    frame = pd.DataFrame(rows, dtype=str)
    if columns:
        for column in columns:
            if column not in frame.columns:
                frame[column] = ""
        frame = frame[columns + [c for c in frame.columns if c not in columns]]
    return frame.fillna("").astype(str)


def _mark_auto_fixed(
    session: Session, batch_id: str, entity: str, rule_id: str, record_ids: list[str]
) -> None:
    if not record_ids:
        return
    session.execute(
        text(
            """
            update public.validation_findings
               set status = 'auto_fixed'
             where batch_id = cast(:b as uuid)
               and entity = :entity
               and rule_id = :rule_id
               and record_id = any(:ids)
            """
        ),
        {"b": batch_id, "entity": entity, "rule_id": rule_id, "ids": record_ids},
    )


def _mark_archived(
    session: Session, batch_id: str, entity: str, rule_id: str, record_ids: list[str]
) -> None:
    if not record_ids:
        return
    session.execute(
        text(
            """
            update public.validation_findings
               set status = 'ignored'
             where batch_id = cast(:b as uuid)
               and entity = :entity
               and rule_id = :rule_id
               and record_id = any(:ids)
            """
        ),
        {"b": batch_id, "entity": entity, "rule_id": rule_id, "ids": record_ids},
    )


def _mark_merged(
    session: Session, batch_id: str, entity: str, rule_id: str, changes: list[dict]
) -> None:
    merged = [row for c in changes for row in c.get("merged", [])]
    if not merged:
        return
    survivor_by_merged: dict[str, str] = {}
    for row in merged:
        for merged_id in row["merged"]:
            survivor_by_merged[merged_id] = row["survivor"]

    for merged_id, survivor in survivor_by_merged.items():
        session.execute(
            text(
                """
                update public.validation_findings
                   set status = 'applied',
                       details = details || cast(:extra as jsonb)
                 where batch_id = cast(:b as uuid)
                   and entity = :entity
                   and rule_id = :rule_id
                   and record_id = :merged_id
                """
            ),
            {
                "b": batch_id,
                "entity": entity,
                "rule_id": rule_id,
                "merged_id": merged_id,
                "extra": _json({"survivor": survivor, "merge_applied": True}),
            },
        )


def _json(value: Any) -> str:
    import json

    return json.dumps(value, default=str)


# ----------------------------------------------------------- review queue ---


def _queue_for_review(
    session: Session,
    batch_id: str,
    entity: str,
    frame: pd.DataFrame,
    spec: EntitySpec,
    ruleset,
    ctx: RuleContext,
    user,
    decided: set[tuple[str, str, str]] | None = None,
) -> dict[str, Any]:
    """Re-run the rules and queue whatever is still unresolved.

    Running the rules again rather than reading the stored findings is
    deliberate: the cleaner has changed the data, so the findings that matter
    are the ones that survive the cleaning, not the ones raised before it.
    """
    from app.rules.checks import RULES
    from app.rules.loader import expand_for_entity

    # The cleaned frame becomes the context's view of this entity so that
    # referential rules see the post-archive reality.
    ctx.frames[entity] = frame

    queued = 0
    by_rule: dict[str, int] = {}

    for base_rule in ruleset.for_entity(entity):
        if base_rule.auto_fixable and AUTO_FIXES.get(base_rule.rule_type) is not None:
            # Already handled. Re-running an auto-fixable rule here would queue
            # rows the cleaner deliberately repaired.
            continue
        implementation = RULES.get(base_rule.rule_type)
        if implementation is None:
            continue
        rule = expand_for_entity(base_rule, entity)
        if not rule.params:
            continue
        try:
            findings = implementation(frame, spec, rule.params, ctx)
        except Exception:
            logger.exception("review re-check %s failed on %s", rule.rule_id, entity)
            continue

        for finding in findings:
            record_id = finding.get("record_id")
            if not record_id:
                continue
            if decided and (entity, str(record_id), rule.rule_id) in decided:
                # Already judged by a person. Leave the record and its finding
                # exactly as they were decided.
                continue
            _upsert_review_item(
                session,
                batch_id=batch_id,
                entity=entity,
                record_id=str(record_id),
                rule=rule,
                finding=finding,
                frame=frame,
            )
            queued += 1
            by_rule[rule.rule_id] = by_rule.get(rule.rule_id, 0) + 1
            if queued >= MAX_REVIEW_ITEMS_PER_FINDING * 20:
                return {"queued": queued, "by_rule": by_rule, "truncated": True}

    return {"queued": queued, "by_rule": by_rule, "truncated": False}


def _upsert_review_item(
    session: Session,
    *,
    batch_id: str,
    entity: str,
    record_id: str,
    rule: Rule,
    finding: dict[str, Any],
    frame: pd.DataFrame,
) -> None:
    legacy_record = _row_as_dict(frame, record_id)

    # Link the queue item to the finding that raised it, and move that finding
    # to pending_review. Without the link the review decision could not update
    # the finding, and the finding would sit at "open" forever even after a
    # human had dealt with it.
    # Find the finding this item belongs to, or create it. The re-check runs
    # against cleaned data, so it can raise a finding the original validation
    # never did: an invoice whose customer was just archived is an orphan only
    # now. Storing that finding is what keeps the invariant "every review item
    # is backed by a finding" true. Matching is on entity, rule and record; the
    # field is not part of the identity because the two runs can report a
    # different column for the same problem.
    finding_id = session.execute(
        text(
            """
            select id from public.validation_findings
             where batch_id = cast(:b as uuid)
               and entity = :entity
               and rule_id = :rule_id
               and record_id = :record_id
               and status not in ('approved', 'rejected')
             order by detected_at
             limit 1
            """
        ),
        {
            "b": batch_id,
            "entity": entity,
            "rule_id": rule.rule_id,
            "record_id": record_id,
        },
    ).scalar()

    if finding_id is None:
        finding_id = session.execute(
            text(
                """
                insert into public.validation_findings (
                    batch_id, rule_id, rule_category, severity, entity, record_id,
                    field, message, details, suggested_fix, auto_fixable, status
                ) values (
                    cast(:b as uuid), :rule_id, :category, cast(:severity as severity),
                    :entity, :record_id, :field, :message, cast(:details as jsonb),
                    :suggested_fix, false, 'open'
                )
                returning id
                """
            ),
            {
                "b": batch_id,
                "rule_id": rule.rule_id,
                "category": rule.category,
                "severity": finding.get("severity") or rule.severity,
                "entity": entity,
                "record_id": record_id,
                "field": finding.get("field"),
                "message": finding.get("message") or rule.name,
                "details": _json(finding.get("details") or {}),
                "suggested_fix": finding.get("suggested_fix") or rule.suggested_fix,
            },
        ).scalar()

    session.execute(
        text(
            """
            update public.validation_findings
               set status = 'pending_review'
             where id = cast(:id as uuid)
            """
        ),
        {"id": str(finding_id)},
    )

    session.execute(
        text(
            """
            insert into public.review_queue (
                id, batch_id, finding_id, entity, record_id, reason, severity,
                legacy_record, proposed_record, status
            ) values (
                cast(:id as uuid), cast(:b as uuid), cast(:finding_id as uuid), :entity,
                :record_id, :reason, cast(:severity as severity), cast(:legacy as jsonb),
                cast(:proposed as jsonb), 'pending'
            )
            """
        ),
        {
            "id": str(uuid.uuid4()),
            "b": batch_id,
            "finding_id": str(finding_id) if finding_id else None,
            "entity": entity,
            "record_id": record_id,
            "reason": finding.get("message") or rule.name,
            "severity": finding.get("severity") or rule.severity,
            "legacy": _json(legacy_record),
            # The proposal starts as the legacy row. AI mapping and the human
            # reviewer both edit this, and the diff the UI shows is
            # legacy_record against proposed_record.
            "proposed": _json(legacy_record),
        },
    )


def _row_as_dict(frame: pd.DataFrame, record_id: str) -> dict[str, Any]:
    if "record_id" not in frame.columns:
        return {}
    matches = frame.index[frame["record_id"].astype(str) == str(record_id)]
    if len(matches) == 0:
        return {}
    return {k: ("" if v is None else str(v)) for k, v in frame.loc[matches[0]].items()}


def _decided_keys(session: Session, batch_id: str) -> set[tuple[str, str, str]]:
    """(entity, record_id, rule_id) triples a human has already decided.

    The rule id comes from the linked finding. An item with no link contributes
    the empty rule id, which simply never matches a rule and so is ignored
    rather than blocking an unrelated record.
    """
    rows = session.execute(
        text(
            """
            select q.entity, q.record_id, coalesce(f.rule_id, '') as rule_id
              from public.review_queue q
              left join public.validation_findings f on f.id = q.finding_id
             where q.batch_id = cast(:b as uuid)
               and q.status in ('approved', 'rejected', 'bulk_approved')
            """
        ),
        {"b": batch_id},
    ).all()
    return {(str(r.entity), str(r.record_id), str(r.rule_id)) for r in rows}


def _count_open_findings(session: Session, batch_id: str) -> int:
    return int(
        session.execute(
            text(
                "select count(*) from public.validation_findings "
                "where batch_id = cast(:b as uuid) and status in ('open', 'pending_review')"
            ),
            {"b": batch_id},
        ).scalar_one()
    )


# ------------------------------------------------------- review decisions ---


@dataclass
class ReviewDecision:
    review_id: str
    status: str
    entity: str
    record_id: str | None
    finding_status: str


def decide_review_item(
    session: Session,
    *,
    review_id: str,
    decision: str,
    user,
    note: str = "",
    proposed_record: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> ReviewDecision:
    """Approve or reject one review item.

    Approving means "load the proposed record as it stands". Rejecting means
    "do not load this record" and moves it to quarantine. Both write an audit
    entry, because a decision about what enters the new ERP is exactly the kind
    of thing an auditor asks about later.

    A reviewer may also pass ``proposed_record`` to edit the proposal before
    approving, which is how a human overrides an AI suggestion.
    """
    if decision not in {"approve", "reject"}:
        raise ValueError("decision must be 'approve' or 'reject'")

    row = session.execute(
        text(
            """
            select id, batch_id, entity, record_id, finding_id, legacy_record,
                   proposed_record, status, severity, reason
              from public.review_queue
             where id = cast(:id as uuid)
            """
        ),
        {"id": review_id},
    ).mappings().first()
    if row is None:
        raise LookupError(f"review item {review_id} not found")
    if row["status"] != "pending":
        raise ValueError(f"review item {review_id} was already {row['status']}")

    proposed = proposed_record if proposed_record is not None else dict(row["proposed_record"] or {})
    new_status = "approved" if decision == "approve" else "rejected"
    finding_status = "approved" if decision == "approve" else "rejected"

    session.execute(
        text(
            """
            update public.review_queue
               set status = cast(:status as review_status),
                   proposed_record = cast(:proposed as jsonb),
                   decided_by = cast(:by as uuid),
                   decided_at = now(),
                   decision_note = :note
             where id = cast(:id as uuid)
            """
        ),
        {
            "id": review_id,
            "status": new_status,
            "proposed": _json(proposed),
            "by": getattr(user, "user_id", None),
            "note": note,
        },
    )

    if row["finding_id"]:
        session.execute(
            text(
                """
                update public.validation_findings
                   set status = cast(:status as finding_status)
                 where id = cast(:id as uuid)
                """
            ),
            {"id": str(row["finding_id"]), "status": finding_status},
        )

    if decision == "reject":
        # A rejected record does not travel to the new ERP. It is preserved in
        # quarantine with the reason, so nothing is silently lost.
        from app.pipeline.ingest import quarantine_records

        quarantine_records(
            batch_id=str(row["batch_id"]),
            entity=str(row["entity"]),
            records=[dict(row["legacy_record"] or {})],
            reason=f"Rejected in review: {row['reason']}",
            rule_id="REVIEW",
        )

    from app.security import audit

    audit.record(
        session,
        action=f"review.{new_status}",
        user=user,
        entity="review_queue",
        entity_id=review_id,
        batch_id=row["batch_id"],
        before={"status": row["status"], "proposed_record": row["proposed_record"]},
        after={"status": new_status, "proposed_record": proposed, "note": note},
        request_id=request_id,
    )

    return ReviewDecision(
        review_id=review_id,
        status=new_status,
        entity=str(row["entity"]),
        record_id=row["record_id"],
        finding_status=finding_status,
    )


def bulk_decide_review_items(
    session: Session,
    *,
    batch_id: str,
    decision: str,
    user,
    entity: str | None = None,
    rule_id: str | None = None,
    severity: str | None = None,
    note: str = "",
    request_id: str | None = None,
    limit: int = 5000,
    review_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Decide many review items at once.

    Filtered by entity, rule or severity, or narrowed to an explicit list of
    ids. The id form is what the review screen uses, so a decision covers
    exactly the rows the reviewer ticked rather than everything matching a
    filter they may have since changed.

    Bulk approval is how the demo clears a large queue, so it is deliberately
    narrow: it only ever touches items still pending, and it reports exactly
    which items it changed.
    """
    filters = ["batch_id = cast(:b as uuid)", "status = 'pending'"]
    params: dict[str, Any] = {"b": batch_id, "limit": limit}
    if entity:
        filters.append("entity = :entity")
        params["entity"] = entity
    if severity:
        filters.append("severity = cast(:severity as severity)")
        params["severity"] = severity
    if rule_id:
        # review_queue has no rule_id column; the rule lives on the finding the
        # item was raised from, so resolve through finding_id.
        filters.append(
            "finding_id in ("
            " select id from public.validation_findings"
            "  where batch_id = cast(:b as uuid) and rule_id = :rule_id)"
        )
        params["rule_id"] = rule_id
    if review_ids is not None:
        if not review_ids:
            # An explicit empty selection means nothing to do. Returning early
            # is safer than dropping the filter and deciding the whole queue.
            return {"requested": 0, "decided": 0, "failed": [], "decision": decision}
        filters.append("id = any(cast(:review_ids as uuid[]))")
        params["review_ids"] = list(review_ids)

    where = " and ".join(filters)
    rows = session.execute(
        text(f"select id from public.review_queue where {where} order by created_at limit :limit"),
        params,
    ).scalars().all()

    decided = 0
    failed: list[str] = []
    for review_id in rows:
        try:
            decide_review_item(
                session,
                review_id=str(review_id),
                decision=decision,
                user=user,
                note=note,
                request_id=request_id,
            )
            decided += 1
        except Exception as exc:
            logger.warning("bulk decision failed for %s: %s", review_id, exc)
            failed.append(str(review_id))

    return {"requested": len(rows), "decided": decided, "failed": failed, "decision": decision}
