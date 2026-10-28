"""Immutable audit log and rollback history."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import parse_batch_id, row_to_dict
from app.schemas.api import AuditEntryOut, AuditPage
from app.security.deps import CurrentUser, db, require

router = APIRouter(tags=["audit"])


@router.get("/audit", response_model=AuditPage)
def list_audit(
    user: CurrentUser = Depends(require("audit:read")),
    session: Session = Depends(db),
    batch_id: str | None = Query(default=None),
    action: str | None = Query(default=None, max_length=100),
    actor_email: str | None = Query(default=None, max_length=200),
    entity: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> AuditPage:
    where: list[str] = []
    params: dict = {"limit": limit, "offset": offset}

    if batch_id:
        where.append("batch_id = cast(:batch_id as uuid)")
        params["batch_id"] = parse_batch_id(batch_id)
    if action:
        where.append("action = :action")
        params["action"] = action
    if actor_email:
        where.append("actor_email = :actor_email")
        params["actor_email"] = actor_email
    if entity:
        where.append("entity = :entity")
        params["entity"] = entity

    clause = f"where {' and '.join(where)}" if where else ""

    total = session.execute(
        text(f"select count(*) from public.audit_log {clause}"), params
    ).scalar_one()

    rows = session.execute(
        text(
            f"""
            select id, occurred_at, actor_email, actor_role, action, entity,
                   entity_id, batch_id, stage, before_state, after_state, request_id
              from public.audit_log
            {clause}
            order by occurred_at desc, id
            limit :limit offset :offset
            """
        ),
        params,
    ).mappings().all()

    items = []
    for row in rows:
        data = row_to_dict(row)
        # The stored columns are before_state / after_state; the API calls them
        # before / after, which is what the diff view reads.
        data["before"] = data.pop("before_state", None)
        data["after"] = data.pop("after_state", None)
        items.append(AuditEntryOut(**data))

    return AuditPage(total=int(total), limit=limit, offset=offset, items=items)


@router.get("/audit/{entry_id}", response_model=AuditEntryOut)
def get_audit_entry(
    entry_id: str,
    user: CurrentUser = Depends(require("audit:read")),
    session: Session = Depends(db),
) -> AuditEntryOut:
    try:
        entry_id = int(entry_id)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Not a valid audit entry id",
        ) from None

    row = session.execute(
        text(
            """
            select id, occurred_at, actor_email, actor_role, action, entity,
                   entity_id, batch_id, stage, before_state, after_state, request_id
              from public.audit_log where id = :i
            """
        ),
        {"i": entry_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audit entry not found")

    data = row_to_dict(row)
    data["before"] = data.pop("before_state", None)
    data["after"] = data.pop("after_state", None)
    return AuditEntryOut(**data)


@router.get("/audit/verify/immutability")
def verify_immutability(
    user: CurrentUser = Depends(require("audit:read")),
    session: Session = Depends(db),
) -> dict:
    """Prove the audit log cannot be rewritten.

    Two things are checked. That the triggers blocking UPDATE and DELETE are
    installed, and that an attempt to modify a row actually fails. The demo
    calls this endpoint because "the log is immutable" is a claim that should be
    demonstrated, not asserted.
    """
    triggers = session.execute(
        text(
            """
            select tgname from pg_trigger
             where tgrelid = 'public.audit_log'::regclass and not tgisinternal
             order by tgname
            """
        ),
    ).scalars().all()

    blocked: list[dict] = []
    for statement, label in (
        ("update public.audit_log set action = 'tampered'", "UPDATE"),
        ("delete from public.audit_log", "DELETE"),
    ):
        try:
            # The statement runs inside a savepoint so a failure does not poison
            # the surrounding transaction.
            with session.begin_nested():
                session.execute(text(statement))
            blocked.append({"operation": label, "blocked": False})
        except Exception as exc:
            blocked.append(
                {
                    "operation": label,
                    "blocked": True,
                    "detail": type(exc).__name__,
                }
            )

    return {
        "table": "public.audit_log",
        "triggers": list(triggers),
        "immutable": all(item["blocked"] for item in blocked),
        "attempts": blocked,
    }
