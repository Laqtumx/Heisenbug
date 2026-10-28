/**
 * The API contract, mirrored from the backend's Pydantic schemas.
 *
 * These types are the frontend's copy of what the server promises. They are
 * written by hand rather than generated so the shape is readable, but they must
 * be kept in step with backend/app/schemas/api.py.
 */

export type Role =
  | "admin"
  | "migration_engineer"
  | "finance_controller"
  | "inventory_manager"
  | "supplier";

export type Severity = "blocker" | "warn" | "info";

export type BatchStatus =
  | "created"
  | "ingesting"
  | "ingested"
  | "profiled"
  | "validating"
  | "validated"
  | "cleaning"
  | "cleaned"
  | "mapping"
  | "mapped"
  | "dry_running"
  | "dry_run_complete"
  | "loading"
  | "loaded"
  | "verifying"
  | "verified"
  | "signed_off"
  | "rolled_back"
  | "failed";

export type StageName =
  | "ingest"
  | "profile"
  | "validate"
  | "clean"
  | "map"
  | "dry_run"
  | "load"
  | "verify"
  | "signoff";

export type StageStatus = "pending" | "running" | "succeeded" | "failed" | "skipped";

export interface User {
  id: string;
  email: string;
  full_name: string;
  role: Role;
  supplier_id: string | null;
  supplier_code: string | null;
  is_active: boolean;
}

export interface Capabilities {
  can_manage_batches: boolean;
  can_ingest: boolean;
  can_validate: boolean;
  can_clean: boolean;
  can_review: boolean;
  can_map: boolean;
  can_load: boolean;
  can_rollback: boolean;
  can_view_finance: boolean;
  can_approve_finance: boolean;
  can_view_payroll: boolean;
  can_view_inventory: boolean;
  can_view_audit: boolean;
  can_view_own_catalog: boolean;
  is_external: boolean;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: User;
}

export interface MeResponse {
  user: User;
  permissions: string[];
  capabilities: Capabilities;
}

export interface Batch {
  id: string;
  batch_code: string;
  name: string;
  source_system: string;
  target_system: string;
  status: BatchStatus;
  health_score_before: number | null;
  health_score_after: number | null;
  record_count_in: number;
  record_count_loaded: number;
  record_count_archived: number;
  record_count_rejected: number;
  created_at: string;
  completed_at: string | null;
}

export interface Stage {
  stage: StageName;
  status: StageStatus;
  attempt: number;
  duration_ms: number | null;
  summary: Record<string, unknown> | null;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface IngestedFile {
  entity: string;
  file_name: string;
  row_count: number;
  checksum_sha256: string;
  created_at: string;
}

export interface BatchDetail extends Batch {
  stages: Stage[];
  ingested: IngestedFile[];
}

export interface StageRunResponse {
  batch_id: string;
  stage: string;
  status: string;
  summary: Record<string, unknown>;
}

export interface Finding {
  id: number;
  batch_id: string;
  rule_id: string;
  rule_category: string;
  severity: Severity;
  entity: string;
  record_id: string;
  field: string | null;
  message: string;
  details: Record<string, unknown> | null;
  suggested_fix: string | null;
  auto_fixable: boolean;
  status: string;
  detected_at: string;
}

export interface FindingPage {
  total: number;
  limit: number;
  offset: number;
  items: Finding[];
}

export interface FindingsSummary {
  batch_id: string;
  total: number;
  by_severity: Record<string, number>;
  by_entity: Record<string, number>;
  by_rule: Record<string, number>;
  by_status: Record<string, number>;
  health_score: number | null;
}

export interface ReviewItem {
  id: string;
  batch_id: string;
  finding_id: number | null;
  entity: string;
  record_id: string;
  reason: string;
  severity: Severity;
  legacy_record: Record<string, string> | null;
  proposed_record: Record<string, string> | null;
  ai_explanation: string | null;
  ai_suggested_fix: Record<string, unknown> | null;
  status: string;
  decided_by: string | null;
  decided_at: string | null;
  decision_note: string | null;
  created_at: string;
}

export interface ReviewPage {
  total: number;
  limit: number;
  offset: number;
  items: ReviewItem[];
}

export interface ReviewDecision {
  review_id: string;
  status: string;
  entity: string;
  record_id: string | null;
  finding_status: string;
}

export interface ReviewBulkOut {
  requested: number;
  decided: number;
  failed: string[];
  decision: string;
}

export interface HealthChecklistItem {
  item: string;
  passed: boolean;
  detail: string;
}

export interface HealthScore {
  batch_id: string;
  health_score: number;
  status: string;
  records_in: number;
  records_loaded: number;
  records_archived: number;
  open_blockers: number;
  go_live_ready: boolean;
  checklist: HealthChecklistItem[];
}

export interface AuditEntry {
  id: number;
  occurred_at: string;
  actor_email: string;
  actor_role: string;
  action: string;
  entity: string;
  entity_id: string | null;
  batch_id: string | null;
  stage: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  request_id: string | null;
}

export interface AuditPage {
  total: number;
  limit: number;
  offset: number;
  items: AuditEntry[];
}

export interface ReconciliationEntity {
  entity: string;
  rows_in: number;
  rows_out: number;
  rows_expected: number;
  counts_match: boolean;
  totals_match: boolean;
  balanced: boolean;
  checksum: string;
  totals_in: Record<string, number>;
  totals_out: Record<string, number>;
}

export interface Reconciliation {
  batch_id: string;
  phase: string;
  health_score: number | string | null;
  counts: Record<string, number | boolean>;
  financial_totals: Record<
    string,
    { in: Record<string, number>; out: Record<string, number> }
  >;
  checksums: Record<string, string>;
  per_entity: Record<string, ReconciliationEntity>;
  balanced: boolean;
  created_at: string;
}

export interface RlsProbe {
  table: string;
  area: string;
  description: string;
  visible: boolean;
  granted: boolean;
  row_count: number | null;
  total_rows: number;
  denied_rows: number;
  denial_reason: string | null;
}

export interface RlsMatrix {
  roles: Record<string, RlsProbe[]>;
  summary: Record<string, string[]>;
  enforced_by: string;
  /** The supplier the external probe ran as, so its zeroes can be read as denials. */
  supplier_code: string | null;
}

export interface StockItem {
  item_code: string;
  item_name: string | null;
  warehouse_code: string;
  qty_on_hand: number;
  reorder_level: number | null;
  stock_out_risk: string | null;
}

export interface InventoryInsights {
  batch_id: string;
  generated_at: string;
  total_items: number;
  at_risk: number;
  below_reorder: StockItem[];
}

export interface SupplierPurchaseOrder {
  po_number: string;
  supplier_code: string;
  order_date: string | null;
  currency: string | null;
  total_amount: string | null;
  status: string | null;
}

export interface SupplierCatalogItem {
  supplier_code: string;
  item_code: string;
  supplier_sku: string | null;
  unit_price: string | null;
  currency: string | null;
  lead_time_days: number | null;
  min_order_qty: string | null;
  is_active: boolean | null;
}

export interface SupplierPortal {
  supplier_code: string;
  purchase_orders: SupplierPurchaseOrder[];
  catalogue: SupplierCatalogItem[];
  purchase_order_count: number;
  total_order_value: number;
  enforced_by: string;
}

export interface BatchSummary {
  batch: Batch;
  findings: {
    total: number;
    by_severity: Record<string, number>;
    by_category: Record<string, number>;
    by_entity: Record<string, number>;
    by_rule: Record<string, number>;
    by_status: Record<string, number>;
  };
  ingested_entities: string[];
}

export interface Rule {
  rule_id: string;
  name: string;
  rule_type: string;
  category: string;
  entities: string[];
  severity: Severity;
  message: string;
  suggested_fix: string;
  auto_fixable: boolean;
  enabled: boolean;
  params: Record<string, unknown>;
}
