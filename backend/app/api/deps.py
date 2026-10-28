"""Shared helpers for the API routers.

Small things every router needs: resolving a batch, reading a batch's frames
back from Mongo, and turning a database row into the shape the schemas promise.
Keeping them here means a router reads as the endpoint it implements rather than
as a pile of plumbing.
"""

from __future__ import annotations

import uuid
from typing import Any

import pandas as pd
from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.pipeline.clean import load_clean_frame
from app.pipeline.entities import ENTITIES_BY_NAME
from app.pipeline.ingest import ingested_entities, load_entity_frame


def parse_batch_id(batch_id: str) -> str:
    try:
        return str(uuid.UUID(str(batch_id)))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"'{batch_id}' is not a valid batch id",
        ) from None


def get_batch_or_404(session: Session, batch_id: str) -> dict[str, Any]:
    row = session.execute(
        text("select * from public.migration_batches where id = cast(:b as uuid)"),
        {"b": batch_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Batch {batch_id} not found"
        )
    return dict(row)


def ingested_entity_names(session: Session, batch_id: str) -> list[str]:
    """Entities this batch actually ingested, in dependency order.

    Only ingested entities are returned, so a stage that loops over the result
    never sees an empty frame for an entity nobody uploaded.
    """
    order = {spec.name: spec.load_order for spec in ENTITIES_BY_NAME.values()}
    names = [row["entity"] for row in ingested_entities(session, batch_id)]
    return sorted((n for n in names if n in ENTITIES_BY_NAME), key=lambda n: order[n])


def raw_frames(session: Session, batch_id: str) -> dict[str, pd.DataFrame]:
    """The raw ingested frames for a batch, for profile and validate."""
    frames: dict[str, pd.DataFrame] = {}
    for name in ingested_entity_names(session, batch_id):
        frame = load_entity_frame(batch_id, name)
        if frame is not None and not frame.empty:
            frames[name] = frame
    return frames


def cleaned_frames(session: Session, batch_id: str) -> dict[str, pd.DataFrame]:
    """The cleaned frames for a batch, for the dry run and load stages.

    These come from the cleaned view in Mongo, not from the raw landing zone:
    the load must carry the cleaned data, which is the entire point of the
    pipeline.
    """
    frames: dict[str, pd.DataFrame] = {}
    for name in ingested_entity_names(session, batch_id):
        frame = load_clean_frame(batch_id, name)
        if frame is not None and not frame.empty:
            frames[name] = frame
    return frames


def jsonable(value: Any) -> Any:
    """Make a database value safe to serialise."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def row_to_dict(row: Any) -> dict[str, Any]:
    return {k: jsonable(v) for k, v in dict(row).items()}


__all__ = [
    "cleaned_frames",
    "get_batch_or_404",
    "ingested_entity_names",
    "jsonable",
    "parse_batch_id",
    "raw_frames",
    "row_to_dict",
]
