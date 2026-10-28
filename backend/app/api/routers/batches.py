"""Batch lifecycle: create, ingest, and the pipeline stages that follow.

Every stage endpoint follows the same shape. It checks the caller's permission,
loads the batch, runs the stage, and returns a summary. The stages themselves
live in ``app.pipeline``; these endpoints are the transport.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import (
    cleaned_frames,
    get_batch_or_404,
    ingested_entity_names,
    parse_batch_id,
    raw_frames,
    row_to_dict,
)
from app.config import settings
from app.pipeline.clean import run_clean
from app.pipeline.entities import ENTITIES_BY_NAME
from app.pipeline.ingest import (
    IngestError,
    create_batch,
    ingest_bytes,
    validate_upload,
)
from app.pipeline.load import (
    migration_health_score,
    latest_reconciliation,
    rollback_batch,
    run_dry_run,
    run_load,
)
from app.pipeline.profile import run_profile
from app.rules.engine import findings_summary, run_validation
from app.schemas.api import (
    BatchCreate,
    BatchDetail,
    BatchOut,
    HealthScoreOut,
    IngestedFileOut,
    RollbackOut,
    StageOut,
    StageRunResponse,
)
from app.security.deps import CurrentUser, db, require

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/batches", tags=["batches"])


# ---------------------------------------------------------------- batches ---


@router.post("", response_model=BatchOut, status_code=status.HTTP_201_CREATED)
def create(
    payload: BatchCreate,
    user: CurrentUser = Depends(require("batch:create")),
    session: Session = Depends(db),
) -> BatchOut:
    try:
        batch = create_batch(
            session,
            name=payload.name,
            user=user,
            source_system=payload.source_system,
            target_system=payload.target_system,
            fiscal_year_from=payload.fiscal_year_from,
            fiscal_year_to=payload.fiscal_year_to,
            notes=payload.notes,
        )
    except IngestError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    # create_batch returns a short row for its own callers; the API promises the
    # full batch, so read it back rather than widening that return shape.
    return BatchOut(**row_to_dict(get_batch_or_404(session, str(batch["id"]))))


@router.get("", response_model=list[BatchOut])
def list_batches(
    user: CurrentUser = Depends(require("batch:read")),
    session: Session = Depends(db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[BatchOut]:
    rows = session.execute(
        text(
            """
            select * from public.migration_batches
             order by created_at desc
             limit :limit offset :offset
            """
        ),
        {"limit": limit, "offset": offset},
    ).mappings().all()
    return [BatchOut(**row_to_dict(row)) for row in rows]


@router.get("/{batch_id}", response_model=BatchDetail)
def get_batch(
    batch_id: str,
    user: CurrentUser = Depends(require("batch:read")),
    session: Session = Depends(db),
) -> BatchDetail:
    batch_id = parse_batch_id(batch_id)
    batch = get_batch_or_404(session, batch_id)

    stages = session.execute(
        text(
            "select * from public.migration_stages where batch_id = cast(:b as uuid) "
            "order by created_at"
        ),
        {"b": batch_id},
    ).mappings().all()
    files = session.execute(
        text(
            "select * from public.ingested_files where batch_id = cast(:b as uuid) "
            "order by entity"
        ),
        {"b": batch_id},
    ).mappings().all()

    detail = BatchDetail(**row_to_dict(batch))
    detail.stages = [StageOut(**row_to_dict(row)) for row in stages]
    detail.ingested = [IngestedFileOut(**row_to_dict(row)) for row in files]
    return detail


@router.delete("/{batch_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_batch(
    batch_id: str,
    user: CurrentUser = Depends(require("batch:delete")),
    session: Session = Depends(db),
) -> Response:
    """Delete a batch and everything it loaded.

    Rows carry a foreign key to the batch, so the database removes the loaded
    data with it. Rolling back first is still the safer habit, because it leaves
    an audit entry naming what was removed.
    """
    batch_id = parse_batch_id(batch_id)
    get_batch_or_404(session, batch_id)
    session.execute(
        text("delete from public.migration_batches where id = cast(:b as uuid)"),
        {"b": batch_id},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ----------------------------------------------------------------- ingest ---


@router.post("/{batch_id}/ingest", response_model=StageRunResponse)
async def ingest(
    batch_id: str,
    entity: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
    user: CurrentUser = Depends(require("ingest:run")),
    session: Session = Depends(db),
) -> StageRunResponse:
    """Upload one entity's legacy file into the raw landing zone.

    The raw bytes go to Mongo untouched. Postgres records what arrived. Nothing
    is interpreted here; interpretation is the profile stage's job.
    """
    batch_id = parse_batch_id(batch_id)
    get_batch_or_404(session, batch_id)

    if entity not in ENTITIES_BY_NAME:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown entity '{entity}'. Known: {sorted(ENTITIES_BY_NAME)}",
        )

    data = await file.read()
    try:
        validate_upload(file.filename or "", data, entity)
    except IngestError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    try:
        result = ingest_bytes(
            session,
            batch_id=batch_id,
            entity=entity,
            file_name=file.filename or f"{entity}.csv",
            data=data,
            user=user,
        )
    except IngestError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    return StageRunResponse(
        batch_id=batch_id,
        stage="ingest",
        status="succeeded",
        summary={
            "entity": entity,
            "rows": result.row_count,
            "warnings": result.warnings,
            "checksum": result.checksum,
        },
    )


# ---------------------------------------------------------------- profile ---


@router.post("/{batch_id}/profile", response_model=StageRunResponse)
def profile(
    batch_id: str,
    user: CurrentUser = Depends(require("profile:run")),
    session: Session = Depends(db),
) -> StageRunResponse:
    batch_id = parse_batch_id(batch_id)
    get_batch_or_404(session, batch_id)
    frames = raw_frames(session, batch_id)
    if not frames:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Nothing has been ingested for this batch yet",
        )
    summary = run_profile(session, batch_id=batch_id, frames=frames, user=user)
    return StageRunResponse(
        batch_id=batch_id, stage="profile", status="succeeded", summary=summary.to_dict()
    )


# --------------------------------------------------------------- validate ---


@router.post("/{batch_id}/validate", response_model=StageRunResponse)
def validate(
    batch_id: str,
    user: CurrentUser = Depends(require("validate:run")),
    session: Session = Depends(db),
) -> StageRunResponse:
    batch_id = parse_batch_id(batch_id)
    get_batch_or_404(session, batch_id)
    frames = raw_frames(session, batch_id)
    if not frames:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Nothing has been ingested for this batch yet",
        )
    result = run_validation(session, batch_id=batch_id, frames=frames, user=user)
    return StageRunResponse(
        batch_id=batch_id,
        stage="validate",
        status="succeeded",
        summary={
            "health_score": result.health_score,
            "findings_by_entity": result.findings_by_entity,
            "findings_by_severity": result.findings_by_severity,
            "records_by_worst_severity": result.records_by_worst_severity,
            "duration_ms": result.duration_ms,
        },
    )


# ------------------------------------------------------------------ clean ---


@router.post("/{batch_id}/clean", response_model=StageRunResponse)
def clean(
    batch_id: str,
    user: CurrentUser = Depends(require("clean:run")),
    session: Session = Depends(db),
) -> StageRunResponse:
    batch_id = parse_batch_id(batch_id)
    get_batch_or_404(session, batch_id)
    frames = raw_frames(session, batch_id)
    if not frames:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Nothing has been ingested for this batch yet",
        )
    _, summary = run_clean(session, batch_id=batch_id, frames=frames, user=user)
    return StageRunResponse(
        batch_id=batch_id, stage="clean", status="succeeded", summary=summary.to_dict()
    )


# --------------------------------------------------------------- dry run ----


@router.post("/{batch_id}/dry-run", response_model=StageRunResponse)
def dry_run(
    batch_id: str,
    user: CurrentUser = Depends(require("dryrun:run")),
    session: Session = Depends(db),
) -> StageRunResponse:
    batch_id = parse_batch_id(batch_id)
    get_batch_or_404(session, batch_id)
    frames = cleaned_frames(session, batch_id)
    if not frames:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This batch has not been cleaned yet, so there is nothing to simulate",
        )
    report = run_dry_run(session, batch_id=batch_id, frames=frames, user=user)
    return StageRunResponse(
        batch_id=batch_id,
        stage="dry_run",
        status="succeeded",
        summary=report.to_dict(),
    )


# ------------------------------------------------------------------- load ---


@router.post("/{batch_id}/load", response_model=StageRunResponse)
def load(
    batch_id: str,
    require_balanced: bool = Query(
        default=True,
        description="Refuse to load unless the dry run reconciled.",
    ),
    user: CurrentUser = Depends(require("load:run")),
    session: Session = Depends(db),
) -> StageRunResponse:
    batch_id = parse_batch_id(batch_id)
    get_batch_or_404(session, batch_id)
    frames = cleaned_frames(session, batch_id)
    if not frames:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This batch has not been cleaned yet, so there is nothing to load",
        )
    try:
        report = run_load(
            session,
            batch_id=batch_id,
            frames=frames,
            user=user,
            require_balanced_dry_run=require_balanced,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return StageRunResponse(
        batch_id=batch_id, stage="load", status="succeeded", summary=report.to_dict()
    )


# --------------------------------------------------------------- rollback ---


@router.post("/{batch_id}/rollback", response_model=RollbackOut)
def rollback(
    batch_id: str,
    user: CurrentUser = Depends(require("rollback:run")),
    session: Session = Depends(db),
) -> RollbackOut:
    batch_id = parse_batch_id(batch_id)
    get_batch_or_404(session, batch_id)
    removed = rollback_batch(session, batch_id=batch_id, user=user)
    return RollbackOut(
        batch_id=batch_id, rows_removed=sum(removed.values()), per_table=removed
    )


# -------------------------------------------------------------- reporting ---


@router.get("/{batch_id}/summary")
def batch_summary(
    batch_id: str,
    user: CurrentUser = Depends(require("batch:read")),
    session: Session = Depends(db),
) -> dict:
    """Everything the wizard header and the dashboard need, in one call."""
    batch_id = parse_batch_id(batch_id)
    batch = get_batch_or_404(session, batch_id)
    summary = findings_summary(session, batch_id)
    return {
        "batch": row_to_dict(batch),
        "findings": summary,
        "ingested_entities": ingested_entity_names(session, batch_id),
    }


@router.get("/{batch_id}/reconciliation")
def reconciliation(
    batch_id: str,
    phase: str | None = Query(default=None, pattern="^(dry_run|load)$"),
    user: CurrentUser = Depends(require("batch:read")),
    session: Session = Depends(db),
) -> dict:
    batch_id = parse_batch_id(batch_id)
    get_batch_or_404(session, batch_id)
    try:
        return latest_reconciliation(session, batch_id, phase=phase)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/{batch_id}/health", response_model=HealthScoreOut)
def health(
    batch_id: str,
    user: CurrentUser = Depends(require("batch:read")),
    session: Session = Depends(db),
) -> HealthScoreOut:
    batch_id = parse_batch_id(batch_id)
    get_batch_or_404(session, batch_id)
    return HealthScoreOut(**migration_health_score(session, batch_id))
