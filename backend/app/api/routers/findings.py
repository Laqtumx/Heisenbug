"""Validation findings: filter, paginate, summarise."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_batch_or_404, parse_batch_id, row_to_dict
from app.rules.engine import findings_summary
from app.schemas.api import FindingOut, FindingPage, FindingsSummary
from app.security.deps import CurrentUser, db, require

router = APIRouter(tags=["findings"])

SORTABLE = {"detected_at", "severity", "entity", "rule_id", "record_id"}


@router.get("/findings", response_model=FindingPage)
def list_findings(
    user: CurrentUser = Depends(require("finding:read")),
    session: Session = Depends(db),
    batch_id: str | None = Query(default=None),
    entity: str | None = Query(default=None),
    severity: str | None = Query(default=None, pattern="^(blocker|warn|info)$"),
    rule_id: str | None = Query(default=None),
    finding_status: str | None = Query(default=None, alias="status"),
    record_id: str | None = Query(default=None),
    auto_fixable: bool | None = Query(default=None),
    search: str | None = Query(default=None, max_length=200),
    sort: str = Query(default="detected_at"),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> FindingPage:
    """Findings, filtered and paginated.

    The filters are built from a fixed set of conditions rather than by
    interpolating the caller's input into SQL, so a filter value cannot become
    part of the query.
    """
    if sort not in SORTABLE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Cannot sort by '{sort}'. Sortable: {sorted(SORTABLE)}",
        )

    where: list[str] = []
    params: dict = {"limit": limit, "offset": offset}

    if batch_id:
        where.append("batch_id = cast(:batch_id as uuid)")
        params["batch_id"] = parse_batch_id(batch_id)
    if entity:
        where.append("entity = :entity")
        params["entity"] = entity
    if severity:
        where.append("severity = cast(:severity as severity)")
        params["severity"] = severity
    if rule_id:
        where.append("rule_id = :rule_id")
        params["rule_id"] = rule_id
    if finding_status:
        where.append("status = :finding_status")
        params["finding_status"] = finding_status
    if record_id:
        where.append("record_id = :record_id")
        params["record_id"] = record_id
    if auto_fixable is not None:
        where.append("auto_fixable = :auto_fixable")
        params["auto_fixable"] = auto_fixable
    if search:
        where.append("(message ilike :search or rule_id ilike :search or record_id ilike :search)")
        params["search"] = f"%{search}%"

    clause = f"where {' and '.join(where)}" if where else ""

    total = session.execute(
        text(f"select count(*) from public.validation_findings {clause}"), params
    ).scalar_one()

    rows = session.execute(
        text(
            f"""
            select * from public.validation_findings
            {clause}
            order by {sort} {order}, id
            limit :limit offset :offset
            """
        ),
        params,
    ).mappings().all()

    return FindingPage(
        total=int(total),
        limit=limit,
        offset=offset,
        items=[FindingOut(**row_to_dict(row)) for row in rows],
    )


@router.get("/findings/summary", response_model=FindingsSummary)
def summary(
    batch_id: str = Query(...),
    user: CurrentUser = Depends(require("finding:read")),
    session: Session = Depends(db),
) -> FindingsSummary:
    """Counts by severity, entity, rule and status, plus the health score.

    The dashboard reads this rather than counting rows itself, so the numbers on
    screen come from the same query the API reports.
    """
    batch_id = parse_batch_id(batch_id)
    batch = get_batch_or_404(session, batch_id)
    data = findings_summary(session, batch_id)
    # The gauge shows the current score, which is the post-clean score once the
    # cleaner has run. Reporting the pre-clean score here made the dashboard
    # contradict the batch list, which reads the after column directly.
    after = batch.get("health_score_after")
    before = batch.get("health_score_before")
    return FindingsSummary(
        batch_id=batch_id,
        total=data.get("total", 0),
        by_severity=data.get("by_severity", {}),
        by_entity=data.get("by_entity", {}),
        by_rule=data.get("by_rule", {}),
        by_status=data.get("by_status", {}),
        health_score=after if after is not None else before,
    )


@router.get("/findings/rules")
def rules_catalogue(
    user: CurrentUser = Depends(require("finding:read")),
) -> dict:
    """The rule set as configured, so the UI can explain what a rule means."""
    from app.rules.loader import load_ruleset

    ruleset = load_ruleset()
    return {
        "version": ruleset.version,
        "rules": [
            {
                "rule_id": r.rule_id,
                "name": r.name,
                "rule_type": r.rule_type,
                "category": r.category,
                "entities": list(r.entities),
                "severity": r.severity,
                "message": r.message,
                "suggested_fix": r.suggested_fix,
                "auto_fixable": r.auto_fixable,
                "enabled": r.enabled,
                "params": r.params,
            }
            for r in ruleset.rules
        ],
    }
