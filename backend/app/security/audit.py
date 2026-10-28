"""Immutable audit log writer.

Every state change the platform makes goes through ``record``. The table itself
rejects UPDATE and DELETE with a trigger, so the trail cannot be rewritten even
by a bug in application code.

Rows are written inside the caller's transaction. If the business change rolls
back, the audit row rolls back with it, which keeps the log honest: it records
what actually happened, not what was attempted.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_MAX_FIELD_CHARS = 20_000


def _truncate(payload: Any) -> Any:
    """Keep audit rows bounded so a huge record cannot bloat the table."""
    if payload is None:
        return None
    try:
        encoded = json.dumps(payload, default=str)
    except (TypeError, ValueError):
        return {"_unserialisable": str(payload)[:_MAX_FIELD_CHARS]}
    if len(encoded) <= _MAX_FIELD_CHARS:
        return payload
    return {"_truncated": True, "_preview": encoded[:_MAX_FIELD_CHARS]}


def record(
    session: Session,
    *,
    action: str,
    user: Any | None = None,
    entity: str = "",
    entity_id: str | None = None,
    batch_id: Any | None = None,
    stage: str | None = None,
    before: Any | None = None,
    after: Any | None = None,
    metadata: dict[str, Any] | None = None,
    request_id: str | None = None,
    ip_address: str | None = None,
) -> None:
    """Append one audit entry. Never raises: a logging failure must not abort work."""
    actor_id = getattr(user, "user_id", None)
    actor_email = getattr(user, "email", None) or "system"
    actor_role = getattr(user, "role", None) or "system"

    try:
        session.execute(
            text(
                """
                insert into public.audit_log (
                    actor_id, actor_email, actor_role, action, entity, entity_id,
                    batch_id, stage, before_state, after_state, metadata,
                    request_id, ip_address
                ) values (
                    cast(:actor_id as uuid), :actor_email, :actor_role, :action,
                    :entity, :entity_id, cast(:batch_id as uuid), :stage,
                    cast(:before_state as jsonb), cast(:after_state as jsonb),
                    cast(:metadata as jsonb), :request_id, :ip_address
                )
                """
            ),
            {
                "actor_id": str(actor_id) if actor_id else None,
                "actor_email": actor_email,
                "actor_role": actor_role,
                "action": action,
                "entity": entity,
                "entity_id": str(entity_id) if entity_id else None,
                "batch_id": str(batch_id) if batch_id else None,
                "stage": stage,
                "before_state": json.dumps(_truncate(before)) if before is not None else None,
                "after_state": json.dumps(_truncate(after)) if after is not None else None,
                "metadata": json.dumps(_truncate(metadata or {})),
                "request_id": request_id,
                "ip_address": ip_address,
            },
        )
    except Exception:  # pragma: no cover - defensive
        logger.exception("Failed to write audit entry for action=%s", action)


def ai_record(
    session: Session,
    *,
    action: str,
    provider: str,
    model: str,
    cache_hit: bool,
    latency_ms: int,
    batch_id: Any | None = None,
    user: Any | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Audit entry for every LLM call, so AI actions are always attributable."""
    record(
        session,
        action=action,
        user=user,
        entity="ai",
        batch_id=batch_id,
        metadata={
            "provider": provider,
            "model": model,
            "cache_hit": cache_hit,
            "latency_ms": latency_ms,
            **(metadata or {}),
        },
    )
