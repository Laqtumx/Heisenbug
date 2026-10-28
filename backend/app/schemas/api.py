"""Pydantic schemas for the REST API.

Request models validate and constrain input; response models pin the shape of
what the API promises, so a change to a pipeline summary cannot silently change
the contract the frontend depends on.

Sensitive values are never in a response model. A supplier's tax id leaves the
API only through an endpoint that checks the caller's permission and decrypts
deliberately.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field
from typing import Annotated

from email_validator import EmailNotValidError, validate_email


def _validate_email(value: str) -> str:
    """Validate an email address, allowing internal and reserved domains.

    ``test_environment=True`` relaxes the deliverability rules that reject
    special-use domains such as ``.test`` and ``.internal``. The syntax is still
    checked, so a malformed address is still refused. Without this the demo
    accounts, which use a reserved domain on purpose, could not sign in.
    """
    try:
        return validate_email(value, check_deliverability=False, test_environment=True).normalized
    except EmailNotValidError as exc:
        raise ValueError(str(exc)) from exc


Email = Annotated[str, AfterValidator(_validate_email)]

Severity = Literal["blocker", "warn", "info"]
Role = Literal[
    "admin", "migration_engineer", "finance_controller", "inventory_manager", "supplier"
]
BatchStatus = Literal[
    "created", "ingesting", "ingested", "profiled", "validating", "validated",
    "cleaning", "cleaned", "mapping", "mapped", "dry_running", "dry_run_complete",
    "loading", "loaded", "verifying", "verified", "signed_off", "rolled_back", "failed",
]
ReviewStatus = Literal["pending", "approved", "rejected", "bulk_approved", "deferred"]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ------------------------------------------------------------------- auth ---


class LoginRequest(BaseModel):
    email: Email
    password: str = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: "UserOut"


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: Role
    supplier_id: uuid.UUID | None = None
    is_active: bool = True


class MeResponse(BaseModel):
    user: UserOut
    permissions: list[str]
    # What the UI uses to decide which nav items and dashboards to render. The
    # server enforces the same rules; this only saves the frontend from guessing.
    capabilities: dict[str, bool]


# ---------------------------------------------------------------- batches ---


class BatchCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    source_system: str = Field(default="legacy_erp", max_length=100)
    target_system: str = Field(default="cleanslate_erp", max_length=100)
    fiscal_year_from: int | None = Field(default=None, ge=1990, le=2100)
    fiscal_year_to: int | None = Field(default=None, ge=1990, le=2100)
    notes: str = Field(default="", max_length=4000)


class BatchOut(BaseModel):
    id: uuid.UUID
    batch_code: str
    name: str
    source_system: str
    target_system: str
    status: str
    health_score_before: float | None = None
    health_score_after: float | None = None
    record_count_in: int = 0
    record_count_loaded: int = 0
    record_count_archived: int = 0
    record_count_rejected: int = 0
    created_at: datetime
    completed_at: datetime | None = None


class StageOut(BaseModel):
    stage: str
    status: str
    attempt: int = 0
    progress: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    summary: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None


class BatchDetail(BatchOut):
    stages: list[StageOut] = Field(default_factory=list)
    ingested: list["IngestedFileOut"] = Field(default_factory=list)


class IngestedFileOut(BaseModel):
    entity: str
    file_name: str
    file_type: str
    row_count: int
    checksum_sha256: str
    created_at: datetime


class StageRunResponse(BaseModel):
    batch_id: uuid.UUID
    stage: str
    status: str
    summary: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------- findings ---


class FindingOut(BaseModel):
    id: uuid.UUID
    rule_id: str
    rule_category: str
    severity: str
    entity: str
    record_id: str
    field: str | None = None
    message: str
    suggested_fix: str | None = None
    auto_fixable: bool = False
    status: str
    details: dict[str, Any] = Field(default_factory=dict)
    detected_at: datetime


class FindingPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[FindingOut]


class FindingsSummary(BaseModel):
    batch_id: uuid.UUID
    total: int
    by_severity: dict[str, int] = Field(default_factory=dict)
    by_entity: dict[str, int] = Field(default_factory=dict)
    by_rule: dict[str, int] = Field(default_factory=dict)
    by_status: dict[str, int] = Field(default_factory=dict)
    health_score: float | None = None


# ----------------------------------------------------------------- review ---


class ReviewItemOut(BaseModel):
    id: uuid.UUID
    batch_id: uuid.UUID
    entity: str
    record_id: str
    reason: str
    severity: str
    status: str
    legacy_record: dict[str, Any] = Field(default_factory=dict)
    proposed_record: dict[str, Any] = Field(default_factory=dict)
    ai_explanation: str | None = None
    ai_suggested_fix: str | None = None
    decided_by: uuid.UUID | None = None
    decided_at: datetime | None = None
    decision_note: str | None = None
    created_at: datetime


class ReviewPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[ReviewItemOut]


class ReviewDecisionRequest(BaseModel):
    note: str = Field(default="", max_length=2000)
    # A reviewer may edit the proposal before approving. This is how a human
    # overrides what the AI suggested.
    proposed_record: dict[str, Any] | None = None


class ReviewBulkRequest(BaseModel):
    decision: Literal["approve", "reject"]
    entity: str | None = None
    severity: Severity | None = None
    note: str = Field(default="", max_length=2000)
    limit: int = Field(default=5000, ge=1, le=50000)


class ReviewDecisionOut(BaseModel):
    review_id: uuid.UUID
    status: str
    entity: str
    record_id: str | None = None
    finding_status: str


class ReviewBulkOut(BaseModel):
    requested: int
    decided: int
    failed: list[str] = Field(default_factory=list)
    decision: str


# ---------------------------------------------------------------- reports ---


class ReconciliationOut(BaseModel):
    batch_id: uuid.UUID
    phase: str
    balanced: bool
    counts: dict[str, Any] = Field(default_factory=dict)
    health_score: float | None = None
    per_entity: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ChecklistItem(BaseModel):
    item: str
    passed: bool
    detail: str


class HealthScoreOut(BaseModel):
    batch_id: uuid.UUID
    health_score: float
    status: str
    records_in: int
    records_loaded: int
    records_archived: int
    open_blockers: int
    checklist: list[ChecklistItem]
    go_live_ready: bool


class RollbackOut(BaseModel):
    batch_id: uuid.UUID
    rows_removed: int
    per_table: dict[str, int] = Field(default_factory=dict)


# ------------------------------------------------------------------ audit ---


class AuditEntryOut(BaseModel):
    id: int
    action: str
    actor_email: str
    actor_role: str
    entity: str
    entity_id: str | None = None
    batch_id: uuid.UUID | None = None
    stage: str | None = None
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    request_id: str | None = None
    occurred_at: datetime


class AuditPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[AuditEntryOut]


# --------------------------------------------------------------- inventory --


class StockOut(BaseModel):
    item_code: str
    item_name: str | None = None
    warehouse_code: str
    qty_on_hand: float
    reorder_level: float | None = None
    stock_out_risk: str | None = None


class InventoryInsightOut(BaseModel):
    batch_id: uuid.UUID
    generated_at: datetime
    total_items: int
    at_risk: int
    below_reorder: list[StockOut] = Field(default_factory=list)


TokenResponse.model_rebuild()
