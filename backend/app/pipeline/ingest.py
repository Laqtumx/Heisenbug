"""Stage 1 — INGEST.

Takes uploaded files, writes the untouched bytes into Mongo as the raw landing
zone, catalogues them in Postgres, and creates the migration batch everything
else hangs off.

The raw copy is deliberate: whatever the cleaning and mapping stages decide, the
original record is always recoverable and the demo can prove a defect was in the
source rather than introduced by CleanSlate.
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.db import mongo
from app.pipeline.entities import ENTITIES_BY_NAME, EntitySpec
from app.pipeline.readers import ReadResult, read_stream
from app.security import audit

logger = logging.getLogger(__name__)

_BATCH_CODE_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class IngestError(ValueError):
    """Raised for anything the caller can fix: bad entity, bad file, too large."""


@dataclass
class IngestedEntity:
    entity: str
    file_name: str
    checksum: str
    row_count: int
    columns: list[str]
    source_format: str
    mongo_document_id: str
    warnings: list[str]


def new_batch_code(prefix: str = "MB") -> str:
    return f"{prefix}-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def validate_upload(file_name: str, data: bytes, entity: str) -> None:
    """Reject a bad upload before any work is done."""
    if entity not in ENTITIES_BY_NAME:
        raise IngestError(
            f"Unknown entity {entity!r}. Known entities: {', '.join(sorted(ENTITIES_BY_NAME))}"
        )
    if not data:
        raise IngestError("Uploaded file is empty.")
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise IngestError(
            f"{file_name} is {len(data) / 1024 / 1024:.1f} MB, "
            f"above the {settings.max_upload_mb} MB limit."
        )
    suffix = Path(file_name).suffix.lower()
    if suffix not in settings.allowed_extensions:
        raise IngestError(
            f"File type {suffix!r} is not allowed. Allowed: {', '.join(settings.allowed_extensions)}"
        )


def create_batch(
    session: Session,
    *,
    name: str,
    user,
    source_system: str = "legacy_erp",
    target_system: str = "cleanslate_erp",
    fiscal_year_from: int | None = None,
    fiscal_year_to: int | None = None,
    notes: str = "",
    batch_code: str | None = None,
    batch_id: str | None = None,
    request_id: str | None = None,
) -> dict:
    code = batch_code or new_batch_code()
    if not _BATCH_CODE_RE.match(code):
        raise IngestError("Batch code may only contain letters, digits, dot, dash and underscore.")

    row = session.execute(
        text(
            """
            insert into public.migration_batches (
                id, batch_code, name, source_system, target_system, status,
                fiscal_year_from, fiscal_year_to, created_by, notes, started_at
            ) values (
                cast(:id as uuid), :code, :name, :source, :target, 'created',
                :fy_from, :fy_to, cast(:created_by as uuid), :notes, now()
            )
            returning id, batch_code, name, status, created_at
            """
        ),
        {
            "id": batch_id or str(uuid.uuid4()),
            "code": code,
            "name": name,
            "source": source_system,
            "target": target_system,
            "fy_from": fiscal_year_from,
            "fy_to": fiscal_year_to,
            "created_by": getattr(user, "user_id", None),
            "notes": notes,
        },
    ).mappings().one()

    for stage in ("ingest", "profile", "validate", "clean", "map", "dry_run", "load", "verify", "signoff"):
        session.execute(
            text(
                """
                insert into public.migration_stages (batch_id, stage, status)
                values (cast(:b as uuid), cast(:s as stage_name), 'pending')
                on conflict (batch_id, stage) do nothing
                """
            ),
            {"b": str(row["id"]), "s": stage},
        )

    audit.record(
        session,
        action="batch.created",
        user=user,
        entity="migration_batches",
        entity_id=str(row["id"]),
        batch_id=row["id"],
        after={"batch_code": code, "name": name},
        request_id=request_id,
    )
    return dict(row)


def start_stage(session: Session, batch_id: str, stage: str) -> None:
    session.execute(
        text(
            """
            update public.migration_stages
               set status = 'running',
                   attempt = attempt + 1,
                   started_at = now(),
                   progress = 0,
                   error_message = null
             where batch_id = cast(:b as uuid) and stage = cast(:s as stage_name)
            """
        ),
        {"b": batch_id, "s": stage},
    )


def finish_stage(
    session: Session,
    batch_id: str,
    stage: str,
    *,
    status: str = "succeeded",
    summary: dict | None = None,
    error: str | None = None,
) -> None:
    session.execute(
        text(
            """
            update public.migration_stages
               set status = cast(:status as stage_status),
                   finished_at = now(),
                   progress = case when cast(:status as stage_status) = 'succeeded' then 100 else progress end,
                   duration_ms = coalesce(
                       extract(epoch from (now() - started_at)) * 1000, 0
                   )::int,
                   summary = cast(:summary as jsonb),
                   error_message = :error
             where batch_id = cast(:b as uuid) and stage = cast(:s as stage_name)
            """
        ),
        {
            "b": batch_id,
            "s": stage,
            "status": status,
            "summary": _json(summary or {}),
            "error": error,
        },
    )


def _json(value) -> str:
    import json

    return json.dumps(value, default=str)


def _frame_to_documents(
    frame: pd.DataFrame, *, batch_id: str, entity: str, file_name: str, checksum: str
) -> list[dict]:
    """Turn a frame into Mongo documents, one per record, plus provenance."""
    ingested_at = datetime.now(UTC).isoformat()
    records = frame.to_dict(orient="records")
    documents = []
    for record in records:
        clean = {k: ("" if v is None else str(v)) for k, v in record.items()}
        record_id = clean.get("record_id") or clean.get(ENTITIES_BY_NAME[entity].primary_key) or ""
        documents.append(
            {
                "_id": f"{batch_id}:{entity}:{record_id}" if record_id else str(uuid.uuid4()),
                "batch_id": batch_id,
                "entity": entity,
                "record_id": str(record_id),
                "source_file": file_name,
                "checksum": checksum,
                "ingested_at": ingested_at,
                "data": clean,
            }
        )
    return documents


def ingest_dataframe(
    session: Session,
    *,
    batch_id: str,
    entity: str,
    frame: pd.DataFrame,
    file_name: str,
    checksum: str,
    source_format: str,
    user,
    warnings: list[str] | None = None,
    request_id: str | None = None,
) -> IngestedEntity:
    """Persist one entity's rows into Mongo and catalogue the file."""
    documents = _frame_to_documents(
        frame, batch_id=batch_id, entity=entity, file_name=file_name, checksum=checksum
    )

    collection = mongo.collection(mongo.RAW_RECORDS)
    # Idempotent re-ingest: same batch + entity + checksum replaces its own
    # documents and nothing else.
    collection.delete_many({"batch_id": batch_id, "entity": entity, "checksum": checksum})
    if documents:
        collection.insert_many(documents, ordered=False)

    manifest_id = str(uuid.uuid4())
    mongo.collection(mongo.INGEST_MANIFESTS).replace_one(
        {"batch_id": batch_id, "entity": entity},
        {
            "_id": manifest_id,
            "batch_id": batch_id,
            "entity": entity,
            "file_name": file_name,
            "checksum": checksum,
            "row_count": len(frame),
            "columns": list(frame.columns),
            "source_format": source_format,
            "ingested_at": datetime.now(UTC).isoformat(),
            "warnings": warnings or [],
        },
        upsert=True,
    )

    session.execute(
        text(
            """
            insert into public.ingested_files (
                batch_id, entity, file_name, file_type, file_size,
                checksum_sha256, mongo_collection, mongo_document_id,
                row_count, uploaded_by
            ) values (
                cast(:b as uuid), :entity, :file_name, :file_type, :size,
                :checksum, 'raw_records', :doc_id, :rows, cast(:user as uuid)
            )
            on conflict (batch_id, entity, checksum_sha256) do update
              set row_count = excluded.row_count,
                  file_name = excluded.file_name,
                  mongo_document_id = excluded.mongo_document_id
            """
        ),
        {
            "b": batch_id,
            "entity": entity,
            "file_name": file_name,
            "file_type": source_format,
            "size": int(frame.memory_usage(deep=True).sum()),
            "checksum": checksum,
            "doc_id": manifest_id,
            "rows": len(frame),
            "user": getattr(user, "user_id", None),
        },
    )

    session.execute(
        text(
            """
            update public.migration_batches
               set status = 'ingested',
                   record_count_in = (
                       select coalesce(sum(row_count), 0)
                         from public.ingested_files
                        where batch_id = cast(:b as uuid)
                   )
             where id = cast(:b as uuid)
            """
        ),
        {"b": batch_id},
    )

    audit.record(
        session,
        action="ingest.entity_loaded",
        user=user,
        entity=entity,
        entity_id=entity,
        batch_id=batch_id,
        stage="ingest",
        after={
            "file_name": file_name,
            "rows": len(frame),
            "columns": list(frame.columns),
            "source_format": source_format,
        },
        request_id=request_id,
    )

    return IngestedEntity(
        entity=entity,
        file_name=file_name,
        checksum=checksum,
        row_count=len(frame),
        columns=list(frame.columns),
        source_format=source_format,
        mongo_document_id=manifest_id,
        warnings=warnings or [],
    )


def ingest_bytes(
    session: Session,
    *,
    batch_id: str,
    entity: str,
    file_name: str,
    data: bytes,
    user,
    request_id: str | None = None,
) -> IngestedEntity:
    validate_upload(file_name, data, entity)
    checksum = hashlib.sha256(data).hexdigest()

    result: ReadResult = read_stream(file_name, data)
    spec: EntitySpec = ENTITIES_BY_NAME[entity]
    _assert_expected_columns(result.frame, spec, result.warnings)

    return ingest_dataframe(
        session,
        batch_id=batch_id,
        entity=entity,
        frame=result.frame,
        file_name=file_name,
        checksum=checksum,
        source_format=result.source_format,
        user=user,
        warnings=result.warnings,
        request_id=request_id,
    )


def _assert_expected_columns(frame: pd.DataFrame, spec: EntitySpec, warnings: list[str]) -> None:
    """Warn about schema drift; only a fully unrecognisable file is fatal."""
    present = set(frame.columns)
    expected = set(spec.column_names)
    missing = expected - present
    extra = present - expected

    if missing:
        warnings.append(f"Columns not found in the file: {', '.join(sorted(missing))}")
    if extra:
        warnings.append(f"Columns not in the target schema: {', '.join(sorted(extra))}")

    key = spec.primary_key
    if key not in present and "record_id" not in present:
        raise IngestError(
            f"File has neither {key!r} nor 'record_id'; cannot identify records. "
            f"Found columns: {', '.join(sorted(present))}"
        )


def load_entity_frame(batch_id: str, entity: str) -> pd.DataFrame:
    """Read an ingested entity back from Mongo as a DataFrame.

    Column order follows the entity spec, so downstream stages see a stable
    shape regardless of how the source file was ordered.
    """
    spec = ENTITIES_BY_NAME.get(entity)
    columns = spec.column_names if spec else []

    cursor = mongo.collection(mongo.RAW_RECORDS).find(
        {"batch_id": batch_id, "entity": entity}, {"data": 1, "record_id": 1, "_id": 0}
    )
    rows = [doc.get("data", {}) for doc in cursor]
    if not rows:
        return pd.DataFrame(columns=columns)

    frame = pd.DataFrame(rows, dtype=str)
    if columns:
        for column in columns:
            if column not in frame.columns:
                frame[column] = ""
        extra = [c for c in frame.columns if c not in columns]
        frame = frame[columns + extra]
    return frame.fillna("").astype(str)


def ingested_entities(session: Session, batch_id: str) -> list[dict]:
    rows = session.execute(
        text(
            """
            select entity, file_name, file_type, row_count, checksum_sha256, created_at
              from public.ingested_files
             where batch_id = cast(:b as uuid)
             order by entity
            """
        ),
        {"b": batch_id},
    ).mappings().all()
    return [dict(r) for r in rows]


def quarantine_records(
    *,
    batch_id: str,
    entity: str,
    records: list[dict],
    reason: str,
    rule_id: str,
) -> int:
    """Move records that cannot be migrated into the quarantine collection."""
    if not records:
        return 0
    now = datetime.now(UTC).isoformat()
    documents = [
        {
            "batch_id": batch_id,
            "entity": entity,
            "record_id": str(record.get("record_id", "")),
            "reason": reason,
            "rule_id": rule_id,
            "quarantined_at": now,
            "record": {k: ("" if v is None else str(v)) for k, v in record.items()},
        }
        for record in records
    ]
    mongo.collection(mongo.QUARANTINE).insert_many(documents, ordered=False)
    return len(documents)
