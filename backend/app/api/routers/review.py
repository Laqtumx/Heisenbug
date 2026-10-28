"""Human review queue.

The review step is where a person decides what enters the new ERP. Everything
the cleaner could not fix safely lands here, side by side with what the AI
proposed, and stays until someone approves or rejects it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_batch_or_404, parse_batch_id, row_to_dict
from app.pipeline.clean import bulk_decide_review_items, decide_review_item
from app.schemas.api import (
    ReviewBulkOut,
    ReviewBulkRequest,
    ReviewDecisionOut,
    ReviewDecisionRequest,
    ReviewItemOut,
    ReviewPage,
)
from app.security.deps import CurrentUser, db, require

router = APIRouter(tags=["review"])

# Who may decide what. A finance controller owns the money records; an inventory
# manager owns stock and items; a migration engineer handles master data. This
# keeps a reviewer from approving a ledger correction they are not accountable
# for.
FINANCE_ENTITIES = {"gl_balances", "invoices", "tax_codes", "chart_of_accounts"}
INVENTORY_ENTITIES = {"inventory_stock", "items", "uom", "categories"}


def _authorise_entity(user: CurrentUser, entity: str) -> None:
    if user.is_admin or user.role == "migration_engineer":
        return
    if entity in FINANCE_ENTITIES and not user.can("finance:approve"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Your role cannot approve {entity} records",
        )
    if entity in INVENTORY_ENTITIES and not user.can("inventory:write"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Your role cannot approve {entity} records",
        )
    if entity == "employees_payroll_summary" and not user.can("payroll:read"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your role cannot review payroll records",
        )


@router.get("/review", response_model=ReviewPage)
def list_review(
    user: CurrentUser = Depends(require("finding:read")),
    session: Session = Depends(db),
    batch_id: str | None = Query(default=None),
    entity: str | None = Query(default=None),
    severity: str | None = Query(default=None, pattern="^(blocker|warn|info)$"),
    review_status: str | None = Query(
        default="pending",
        alias="status",
        pattern="^(pending|approved|rejected|bulk_approved|deferred|all)$",
    ),
    reason: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> ReviewPage:
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
    if review_status and review_status != "all":
        where.append("status = :review_status")
        params["review_status"] = review_status
    if reason:
        where.append("reason = :reason")
        params["reason"] = reason

    clause = f"where {' and '.join(where)}" if where else ""

    total = session.execute(
        text(f"select count(*) from public.review_queue {clause}"), params
    ).scalar_one()

    rows = session.execute(
        text(
            f"""
            select * from public.review_queue
            {clause}
            order by
              case severity when 'blocker' then 0 when 'warn' then 1 else 2 end,
              entity, record_id
            limit :limit offset :offset
            """
        ),
        params,
    ).mappings().all()

    return ReviewPage(
        total=int(total),
        limit=limit,
        offset=offset,
        items=[ReviewItemOut(**_review_row(row)) for row in rows],
    )


def _review_row(row) -> dict:
    """Map a queue row onto the API shape.

    The stored column is ``ai_suggestion``; the API calls it
    ``ai_suggested_fix`` to say what it is. Both are kept because the frontend
    shows the AI's suggestion next to the reviewer's own edit.
    """
    data = row_to_dict(row)
    data["ai_suggested_fix"] = data.pop("ai_suggestion", None)
    return data


@router.get("/review/{review_id}", response_model=ReviewItemOut)
def get_review_item(
    review_id: str,
    user: CurrentUser = Depends(require("finding:read")),
    session: Session = Depends(db),
) -> ReviewItemOut:
    row = session.execute(
        text("select * from public.review_queue where id = cast(:r as uuid)"),
        {"r": _uuid_or_422(review_id, "review id")},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review item not found")
    return ReviewItemOut(**_review_row(row))


@router.post("/review/{review_id}/approve", response_model=ReviewDecisionOut)
def approve(
    review_id: str,
    payload: ReviewDecisionRequest,
    user: CurrentUser = Depends(require("review:decide")),
    session: Session = Depends(db),
) -> ReviewDecisionOut:
    return _decide(review_id, "approve", payload, user, session)


@router.post("/review/{review_id}/reject", response_model=ReviewDecisionOut)
def reject(
    review_id: str,
    payload: ReviewDecisionRequest,
    user: CurrentUser = Depends(require("review:decide")),
    session: Session = Depends(db),
) -> ReviewDecisionOut:
    return _decide(review_id, "reject", payload, user, session)


def _decide(
    review_id: str,
    decision: str,
    payload: ReviewDecisionRequest,
    user: CurrentUser,
    session: Session,
) -> ReviewDecisionOut:
    review_id = _uuid_or_422(review_id, "review id")
    row = session.execute(
        text("select entity, status from public.review_queue where id = cast(:r as uuid)"),
        {"r": review_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review item not found")

    _authorise_entity(user, row["entity"])

    try:
        result = decide_review_item(
            session,
            review_id=review_id,
            decision=decision,
            user=user,
            note=payload.note,
            proposed_record=payload.proposed_record,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return ReviewDecisionOut(
        review_id=review_id,
        status=result.status,
        entity=result.entity,
        record_id=result.record_id,
        finding_status=result.finding_status,
    )


@router.post("/review/bulk", response_model=ReviewBulkOut)
def bulk(
    payload: ReviewBulkRequest,
    user: CurrentUser = Depends(require("review:decide")),
    session: Session = Depends(db),
    batch_id: str = Query(...),
) -> ReviewBulkOut:
    """Approve or reject many items at once.

    A reviewer who has seen a hundred identical findings should not click a
    hundred times. Each item still gets its own audit entry, so a bulk decision
    is traceable item by item.
    """
    batch_id = parse_batch_id(batch_id)
    get_batch_or_404(session, batch_id)

    if payload.entity:
        _authorise_entity(user, payload.entity)

    result = bulk_decide_review_items(
        session,
        batch_id=batch_id,
        decision=payload.decision,
        user=user,
        entity=payload.entity,
        severity=payload.severity,
        note=payload.note,
        limit=payload.limit,
        rule_id=payload.rule_id,
        review_ids=[str(rid) for rid in payload.review_ids] if payload.review_ids is not None else None,
    )
    return ReviewBulkOut(
        requested=result["requested"],
        decided=result["decided"],
        failed=result["failed"],
        decision=payload.decision,
    )


def _uuid_or_422(value: str, label: str) -> str:
    import uuid

    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"'{value}' is not a valid {label}",
        ) from None
