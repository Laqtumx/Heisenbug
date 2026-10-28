"""MongoDB landing zone.

Mongo holds the raw, as-is legacy records and the quarantine set. Nothing in
Mongo is ever mutated: a re-ingest writes a new immutable document set keyed by
(batch_id, entity, checksum). Postgres holds the derived, structured view.
"""

from __future__ import annotations

from typing import Any

from pymongo import ASCENDING, MongoClient
from pymongo.collection import Collection
from pymongo.database import Database

from app.config import settings

RAW_RECORDS = "raw_records"
QUARANTINE = "quarantine"
AI_SUGGESTIONS = "ai_suggestions"
INGEST_MANIFESTS = "ingest_manifests"

_client: MongoClient | None = None


def get_client() -> MongoClient:
    global _client
    if _client is None:
        _client = MongoClient(
            settings.mongo_url,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            uuidRepresentation="standard",
        )
    return _client


def get_db() -> Database:
    return get_client()[settings.mongo_db]


def collection(name: str) -> Collection:
    return get_db()[name]


def ensure_indexes() -> None:
    collection(RAW_RECORDS).create_index(
        [("batch_id", ASCENDING), ("entity", ASCENDING), ("checksum", ASCENDING)]
    )
    collection(RAW_RECORDS).create_index([("batch_id", ASCENDING), ("record_id", ASCENDING)])
    collection(QUARANTINE).create_index([("batch_id", ASCENDING), ("entity", ASCENDING)])
    collection(QUARANTINE).create_index([("batch_id", ASCENDING), ("record_id", ASCENDING)])
    collection(AI_SUGGESTIONS).create_index(
        [("batch_id", ASCENDING), ("kind", ASCENDING), ("cache_key", ASCENDING)]
    )
    collection(INGEST_MANIFESTS).create_index([("batch_id", ASCENDING), ("entity", ASCENDING)])


def healthcheck() -> dict[str, Any]:
    try:
        get_client().admin.command("ping")
        return {
            "mongo": "ok",
            "collections": sorted(get_db().list_collection_names()),
        }
    except Exception as exc:  # pragma: no cover - surfaced to /health
        return {"mongo": "error", "detail": str(exc)}


def close() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None
