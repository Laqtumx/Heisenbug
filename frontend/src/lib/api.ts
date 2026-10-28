/**
 * The API client.
 *
 * All calls go to /api/v1/... on the same origin and are proxied to FastAPI by
 * next.config.mjs. Keeping one origin means no CORS configuration and no token
 * in a cross-origin request.
 *
 * Every method throws ApiError on failure, so callers can distinguish a refused
 * permission from a missing record and say so plainly instead of showing a
 * generic failure.
 */

import type {
  AuditPage,
  Batch,
  BatchDetail,
  BatchSummary,
  FindingPage,
  FindingsSummary,
  HealthScore,
  InventoryInsights,
  MeResponse,
  Reconciliation,
  ReviewBulkOut,
  ReviewDecision,
  ReviewPage,
  RlsMatrix,
  Rule,
  StageRunResponse,
  SupplierPortal,
  TokenResponse,
} from "./types";

export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;

  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }

  get isAuthError() {
    return this.status === 401;
  }

  get isForbidden() {
    return this.status === 403;
  }

  get isMissing() {
    return this.status === 404;
  }

  get isConflict() {
    return this.status === 409;
  }
}

/** Turn the backend's several error shapes into one readable sentence. */
function describeError(status: number, body: unknown): string {
  if (typeof body === "string" && body.trim()) return body;
  if (body && typeof body === "object") {
    const record = body as Record<string, unknown>;
    const detail = record.detail;

    if (typeof detail === "string") return detail;

    if (detail && typeof detail === "object") {
      const d = detail as Record<string, unknown>;
      // The permission refusal shape, so a 403 explains itself.
      if (Array.isArray(d.missing) && d.missing.length) {
        return `This action needs the ${(d.missing as string[]).join(", ")} permission, which your role does not have.`;
      }
      if (typeof d.error === "string") return d.error.replace(/_/g, " ");
      if (typeof d.detail === "string") return d.detail;
    }

    // FastAPI validation errors arrive as a list.
    if (Array.isArray(detail) && detail.length) {
      const first = detail[0] as Record<string, unknown>;
      const loc = Array.isArray(first.loc) ? first.loc.join(".") : "";
      const msg = typeof first.msg === "string" ? first.msg : "Invalid value";
      return loc ? `${loc}: ${msg}` : msg;
    }
  }
  if (status === 401) return "Your session has expired. Sign in again.";
  if (status === 403) return "Your role does not permit this action.";
  if (status === 404) return "Not found.";
  if (status === 409) return "The batch is not in a state that allows this.";
  if (status === 413) return "The upload is larger than the server accepts.";
  return `Request failed with status ${status}.`;
}

async function request<T>(
  path: string,
  options: RequestInit & { token?: string | null } = {},
): Promise<T> {
  const { token, headers, ...rest } = options;
  const response = await fetch(`/api/v1${path}`, {
    ...rest,
    headers: {
      ...(rest.body && !(rest.body instanceof FormData)
        ? { "Content-Type": "application/json" }
        : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(headers ?? {}),
    },
    cache: "no-store",
  });

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }

  if (!response.ok) {
    throw new ApiError(response.status, describeError(response.status, body), body);
  }
  return body as T;
}

function query(params: Record<string, string | number | boolean | undefined | null>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") {
      search.set(key, String(value));
    }
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

// Deliberately not exported from types.ts: this is a request body, not a
// response, so it lives with the client that sends it.
export interface BatchCreate {
  name: string;
  source_system?: string;
  target_system?: string;
  fiscal_year_from?: number | null;
  fiscal_year_to?: number | null;
  notes?: string;
}

export const api = {
  // --------------------------------------------------------------- auth ---
  login(email: string, password: string) {
    return request<TokenResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
  },

  me(token: string) {
    return request<MeResponse>("/auth/me", { token });
  },

  logout(token: string) {
    return request<{ status: string }>("/auth/logout", { method: "POST", token });
  },

  // ------------------------------------------------------------ batches ---
  listBatches(token: string, params: { limit?: number; offset?: number; status?: string } = {}) {
    return request<Batch[]>(`/batches${query(params)}`, { token });
  },

  createBatch(token: string, body: BatchCreate) {
    return request<Batch>("/batches", {
      method: "POST",
      token,
      body: JSON.stringify(body),
    });
  },

  getBatch(token: string, batchId: string) {
    return request<BatchDetail>(`/batches/${batchId}`, { token });
  },

  deleteBatch(token: string, batchId: string) {
    return request<void>(`/batches/${batchId}`, { method: "DELETE", token });
  },

  batchSummary(token: string, batchId: string) {
    return request<BatchSummary>(`/batches/${batchId}/summary`, { token });
  },

  // -------------------------------------------------------------- stages ---
  /**
   * Upload one entity's file.
   *
   * The endpoint takes a single file together with the entity it belongs to,
   * because the entity is what decides the target columns. Callers uploading
   * several files call this once per file.
   */
  ingest(token: string, batchId: string, entity: string, file: File) {
    const form = new FormData();
    form.append("entity", entity);
    form.append("file", file);
    return request<StageRunResponse>(`/batches/${batchId}/ingest`, {
      method: "POST",
      token,
      body: form,
    });
  },

  runStage(token: string, batchId: string, stage: "profile" | "validate" | "clean" | "dry-run" | "load") {
    return request<StageRunResponse>(`/batches/${batchId}/${stage}`, {
      method: "POST",
      token,
    });
  },

  rollback(token: string, batchId: string) {
    return request<StageRunResponse>(`/batches/${batchId}/rollback`, {
      method: "POST",
      token,
    });
  },

  // ------------------------------------------------------------ findings ---
  findings(
    token: string,
    params: {
      batch_id?: string;
      severity?: string;
      entity?: string;
      rule_id?: string;
      status?: string;
      q?: string;
      limit?: number;
      offset?: number;
    } = {},
  ) {
    return request<FindingPage>(`/findings${query(params)}`, { token });
  },

  findingsSummary(token: string, batchId: string) {
    return request<FindingsSummary>(`/findings/summary${query({ batch_id: batchId })}`, {
      token,
    });
  },

  rules(token: string) {
    return request<Rule[]>("/findings/rules", { token });
  },

  // -------------------------------------------------------------- review ---
  reviewQueue(
    token: string,
    params: { batch_id?: string; status?: string; entity?: string; severity?: string; limit?: number; offset?: number } = {},
  ) {
    return request<ReviewPage>(`/review${query(params)}`, { token });
  },

  reviewItem(token: string, reviewId: string) {
    return request<ReviewItemResponse>(`/review/${reviewId}`, { token });
  },

  decideReview(
    token: string,
    reviewId: string,
    decision: "approve" | "reject",
    note?: string,
  ) {
    return request<ReviewDecision>(`/review/${reviewId}/${decision}`, {
      method: "POST",
      token,
      body: JSON.stringify({ note: note ?? "" }),
    });
  },

  /**
   * Approve or reject every pending item matching a filter.
   *
   * The endpoint takes a filter rather than a list of ids, so the batch it
   * decides is the batch the reviewer was looking at. The ids are sent too, so
   * the decision is bounded to what was on screen.
   */
  bulkApprove(
    token: string,
    params: { batchId: string; decision: "approve" | "reject"; note?: string; reviewIds?: string[] },
  ) {
    return request<ReviewBulkOut>(
      `/review/bulk${query({ batch_id: params.batchId })}`,
      {
        method: "POST",
        token,
        body: JSON.stringify({
          decision: params.decision,
          note: params.note ?? "",
          review_ids: params.reviewIds?.length ? params.reviewIds : undefined,
        }),
      },
    );
  },

  // ------------------------------------------------------------- reports ---
  health(token: string, batchId: string) {
    return request<HealthScore>(`/batches/${batchId}/health`, { token });
  },

  reconciliation(token: string, batchId: string, phase = "load") {
    return request<Reconciliation>(`/batches/${batchId}/reconciliation${query({ phase })}`, {
      token,
    });
  },

  reportCsvUrl(batchId: string) {
    return `/api/v1/reports/${batchId}/findings.csv`;
  },

  async reportCsv(token: string, batchId: string) {
    const response = await fetch(this.reportCsvUrl(batchId), {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    });
    if (!response.ok) throw new ApiError(response.status, "The report could not be built.");
    return response.blob();
  },

  // --------------------------------------------------------------- audit ---
  audit(
    token: string,
    params: { batch_id?: string; actor_email?: string; action?: string; entity?: string; limit?: number; offset?: number } = {},
  ) {
    return request<AuditPage>(`/audit${query(params)}`, { token });
  },

  /**
   * Ask the database to prove the audit log cannot be rewritten.
   *
   * The endpoint lists the installed triggers, then tries an UPDATE and a
   * DELETE and reports whether each was refused.
   */
  auditImmutability(token: string) {
    return request<{
      table: string;
      triggers: string[];
      immutable: boolean;
      attempts: { operation: string; blocked: boolean; detail?: string }[];
    }>("/audit/verify/immutability", { token });
  },

  // ------------------------------------------------------------ security ---
  rlsMatrix(token: string, supplierCode?: string) {
    return request<RlsMatrix>(`/security/rls-matrix${query({ supplier_code: supplierCode })}`, {
      token,
    });
  },

  // ----------------------------------------------------------- inventory ---
  inventoryInsights(token: string, batchId: string, limit = 50) {
    return request<InventoryInsights>(
      `/inventory/insights${query({ batch_id: batchId, limit })}`,
      { token },
    );
  },

  // ------------------------------------------------------------ supplier ---
  supplierPortal(token: string) {
    return request<SupplierPortal>("/supplier/portal", { token });
  },
};

export interface ReviewItemResponse {
  id: string;
  batch_id: string;
  finding_id: number | null;
  entity: string;
  record_id: string;
  reason: string;
  severity: string;
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
