"""Stage 3 — VALIDATE.

Runs every applicable rule against every entity, records each finding, and
computes the Migration Health Score.

The score is a weighted measure of how much of the source data survives
validation untouched. It is deliberately pessimistic: a blocker costs far more
than an info, and the score is capped at 100. That is what makes the demo's
"41 to 96" progression meaningful rather than cosmetic.
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.pipeline.entities import ENTITIES_BY_NAME, EntitySpec
from app.rules.checks import RULES, RuleContext
from app.rules.loader import Rule, expand_for_entity, load_ruleset, validate_rule_types
from app.security import audit

logger = logging.getLogger(__name__)

# A blocker means the record cannot be loaded as-is. Weighted heavily so the
# health score reflects load readiness rather than raw issue count.
SEVERITY_WEIGHT = {"blocker": 10.0, "warn": 3.0, "info": 1.0}

# Points deducted per 1% of records carrying a finding at that severity.
SEVERITY_PENALTY_PER_PCT = {"blocker": 1.4, "warn": 0.55, "info": 0.18}


@dataclass
class ValidationResult:
    batch_id: str
    findings_by_entity: dict[str, int]
    findings_by_severity: dict[str, int]
    findings_by_category: dict[str, int]
    findings_by_rule: dict[str, int]
    health_score: float
    total_records: int
    affected_records: int
    duration_ms: int
    rules_run: int
    rule_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "total_records": self.total_records,
            "affected_records": self.affected_records,
            "affected_pct": round(self.affected_records / self.total_records * 100, 2)
            if self.total_records
            else 0.0,
            "health_score": self.health_score,
            "rules_run": self.rules_run,
            "duration_ms": self.duration_ms,
            "findings_by_entity": self.findings_by_entity,
            "findings_by_severity": self.findings_by_severity,
            "findings_by_category": self.findings_by_category,
            "findings_by_rule": self.findings_by_rule,
            "rule_errors": self.rule_errors,
        }


def compute_health_score(
    *,
    total_records: int,
    findings_by_severity: dict[str, int],
    affected_records: int,
) -> float:
    """A 0-100 score for how ready the batch is to load.

    Two pressures push the score down:

    * the share of records carrying at least one finding (breadth), and
    * the severity mix of the findings themselves (depth).

    A batch with a handful of info findings on 50,000 records scores in the 90s.
    A batch where a third of the records are blocked scores in the 40s, which is
    what the demo starts from.
    """
    if total_records <= 0:
        return 0.0

    affected_pct = min(affected_records / total_records * 100, 100.0)
    breadth_penalty = affected_pct * 0.65

    depth_penalty = 0.0
    for severity, count in findings_by_severity.items():
        weight = SEVERITY_WEIGHT.get(severity, 1.0)
        # Normalise per 1,000 records so a big batch is not punished for size.
        rate = count / total_records * 1000
        depth_penalty += min(rate * weight * 0.02, 12.0)

    score = 100.0 - breadth_penalty - depth_penalty
    return round(max(0.0, min(100.0, score)), 2)


def _persist_findings(
    session: Session,
    batch_id: str,
    entity: str,
    rule: Rule,
    findings: list[dict],
) -> None:
    if not findings:
        return

    payload = [
        {
            "batch_id": batch_id,
            "rule_id": rule.rule_id,
            "rule_category": rule.category,
            "severity": f["severity"],
            "entity": entity,
            "record_id": f.get("record_id"),
            "field": f.get("field"),
            "message": f["message"],
            "details": json.dumps(f.get("details") or {}, default=str),
            "suggested_fix": f.get("suggested_fix") or rule.suggested_fix,
            "auto_fixable": bool(f.get("auto_fixable", rule.auto_fixable)),
        }
        for f in findings
    ]

    session.execute(
        text(
            """
            insert into public.validation_findings (
                batch_id, rule_id, rule_category, severity, entity, record_id,
                field, message, details, suggested_fix, auto_fixable
            ) values (
                cast(:batch_id as uuid), :rule_id, :rule_category,
                cast(:severity as severity), :entity, :record_id, :field, :message,
                cast(:details as jsonb), :suggested_fix, :auto_fixable
            )
            """
        ),
        payload,
    )


def run_validation(
    session: Session,
    *,
    batch_id: str,
    frames: dict[str, pd.DataFrame],
    user=None,
    request_id: str | None = None,
    reference_date=None,
    clear_existing: bool = True,
) -> ValidationResult:
    started = time.perf_counter()

    from app.pipeline.ingest import finish_stage, start_stage

    start_stage(session, batch_id, "validate")

    ruleset = load_ruleset()
    unknown = validate_rule_types(set(RULES))
    if unknown:
        raise ValueError(f"Rule types with no implementation: {unknown}")

    if clear_existing:
        # Re-running validation replaces its own findings rather than stacking
        # duplicates. Decisions already made in the review queue survive.
        session.execute(
            text("delete from public.validation_findings where batch_id = cast(:b as uuid)"),
            {"b": batch_id},
        )

    ctx = RuleContext(frames=frames, specs=ENTITIES_BY_NAME, reference_date=reference_date)

    total_records = int(sum(len(f) for f in frames.values()))
    by_severity: Counter[str] = Counter()
    by_category: Counter[str] = Counter()
    by_rule: Counter[str] = Counter()
    by_entity: Counter[str] = Counter()
    affected_records: set[str] = set()
    rule_errors: list[str] = []
    rules_run = 0

    for entity, frame in frames.items():
        spec = ENTITIES_BY_NAME.get(entity)
        if spec is None:
            logger.warning("no entity spec for %r; skipping validation", entity)
            continue

        for base_rule in ruleset.for_entity(entity):
            implementation = RULES.get(base_rule.rule_type)
            if implementation is None:
                rule_errors.append(f"{base_rule.rule_id}: no implementation for {base_rule.rule_type}")
                continue

            rule = expand_for_entity(base_rule, entity)
            # A rule whose parameters resolved to nothing has no columns to
            # check on this entity, which is normal, not an error.
            if not rule.params:
                continue

            try:
                findings = implementation(frame, spec, rule.params, ctx)
            except Exception as exc:  # one bad rule must not sink the batch
                logger.exception("rule %s failed on %s", rule.rule_id, entity)
                rule_errors.append(f"{rule.rule_id} on {entity}: {exc}")
                continue

            rules_run += 1
            if not findings:
                continue

            for finding in findings:
                finding["severity"] = finding.get("severity") or rule.severity
                by_severity[finding["severity"]] += 1
                by_category[rule.category] += 1
                by_rule[rule.rule_id] += 1
                by_entity[entity] += 1
                if finding.get("record_id"):
                    affected_records.add(f"{entity}:{finding['record_id']}")

            _persist_findings(session, batch_id, entity, rule, findings)

    duration_ms = int((time.perf_counter() - started) * 1000)
    health_score = compute_health_score(
        total_records=total_records,
        findings_by_severity=dict(by_severity),
        affected_records=len(affected_records),
    )

    result = ValidationResult(
        batch_id=batch_id,
        findings_by_entity=dict(by_entity),
        findings_by_severity=dict(by_severity),
        findings_by_category=dict(by_category),
        findings_by_rule=dict(by_rule),
        health_score=health_score,
        total_records=total_records,
        affected_records=len(affected_records),
        duration_ms=duration_ms,
        rules_run=rules_run,
        rule_errors=rule_errors,
    )

    summary = result.to_dict()
    summary["total_findings"] = int(sum(by_severity.values()))

    finish_stage(session, batch_id, "validate", summary=summary)
    session.execute(
        text(
            """
            update public.migration_batches
               set status = 'validated', health_score_before = :score
             where id = cast(:b as uuid)
            """
        ),
        {"b": batch_id, "score": health_score},
    )

    audit.record(
        session,
        action="validate.completed",
        user=user,
        entity="validation_findings",
        batch_id=batch_id,
        stage="validate",
        after={
            "health_score": health_score,
            "total_findings": int(sum(by_severity.values())),
            "by_severity": dict(by_severity),
            "rules_run": rules_run,
        },
        request_id=request_id,
    )

    return result


def findings_summary(session: Session, batch_id: str) -> dict[str, Any]:
    rows = session.execute(
        text(
            """
            select severity::text as severity, rule_category, entity, count(*) as count
              from public.validation_findings
             where batch_id = cast(:b as uuid)
             group by severity, rule_category, entity
            """
        ),
        {"b": batch_id},
    ).mappings().all()

    by_severity: Counter[str] = Counter()
    by_category: Counter[str] = Counter()
    by_entity: Counter[str] = Counter()
    for row in rows:
        by_severity[row["severity"]] += row["count"]
        by_category[row["rule_category"]] += row["count"]
        by_entity[row["entity"]] += row["count"]

    return {
        "by_severity": dict(by_severity),
        "by_category": dict(by_category),
        "by_entity": dict(by_entity),
        "total": int(sum(by_severity.values())),
    }
