"use client";

/**
 * Query hooks, one per API call the UI makes.
 *
 * Each hook takes the session token and stays idle until it is present, so a
 * signed-out render never fires a request. Query keys include the batch id
 * wherever the answer depends on it, so switching batches cannot show the
 * previous batch's numbers.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type BatchCreate } from "./api";
import { useSession } from "./session";

export function useBatches(params: { limit?: number; offset?: number } = {}) {
  const { token } = useSession();
  return useQuery({
    queryKey: ["batches", params],
    queryFn: () => api.listBatches(token as string, params),
    enabled: Boolean(token),
  });
}

export function useBatch(batchId: string | undefined) {
  const { token } = useSession();
  return useQuery({
    queryKey: ["batch", batchId],
    queryFn: () => api.getBatch(token as string, batchId as string),
    enabled: Boolean(token && batchId),
  });
}

export function useBatchSummary(batchId: string | undefined) {
  const { token } = useSession();
  return useQuery({
    queryKey: ["batch-summary", batchId],
    queryFn: () => api.batchSummary(token as string, batchId as string),
    enabled: Boolean(token && batchId),
  });
}

export function useFindings(params: {
  batch_id?: string;
  severity?: string;
  entity?: string;
  rule_id?: string;
  status?: string;
  q?: string;
  limit?: number;
  offset?: number;
}) {
  const { token } = useSession();
  return useQuery({
    queryKey: ["findings", params],
    queryFn: () => api.findings(token as string, params),
    enabled: Boolean(token && params.batch_id),
  });
}

export function useFindingsSummary(batchId: string | undefined) {
  const { token } = useSession();
  return useQuery({
    queryKey: ["findings-summary", batchId],
    queryFn: () => api.findingsSummary(token as string, batchId as string),
    enabled: Boolean(token && batchId),
  });
}

export function useRules() {
  const { token } = useSession();
  return useQuery({
    queryKey: ["rules"],
    queryFn: () => api.rules(token as string),
    enabled: Boolean(token),
  });
}

export function useReviewQueue(params: {
  batch_id?: string;
  status?: string;
  entity?: string;
  severity?: string;
  limit?: number;
  offset?: number;
}) {
  const { token } = useSession();
  return useQuery({
    queryKey: ["review", params],
    queryFn: () => api.reviewQueue(token as string, params),
    enabled: Boolean(token),
  });
}

export function useHealth(batchId: string | undefined) {
  const { token } = useSession();
  return useQuery({
    queryKey: ["health", batchId],
    queryFn: () => api.health(token as string, batchId as string),
    enabled: Boolean(token && batchId),
  });
}

export function useReconciliation(batchId: string | undefined, phase = "load") {
  const { token } = useSession();
  return useQuery({
    queryKey: ["reconciliation", batchId, phase],
    queryFn: () => api.reconciliation(token as string, batchId as string, phase),
    enabled: Boolean(token && batchId),
    retry: false,
  });
}

export function useAudit(params: {
  batch_id?: string;
  actor_email?: string;
  action?: string;
  entity?: string;
  limit?: number;
  offset?: number;
}) {
  const { token } = useSession();
  return useQuery({
    queryKey: ["audit", params],
    queryFn: () => api.audit(token as string, params),
    enabled: Boolean(token),
  });
}

export function useAuditImmutability() {
  const { token } = useSession();
  return useQuery({
    queryKey: ["audit-immutability"],
    queryFn: () => api.auditImmutability(token as string),
    enabled: Boolean(token),
  });
}

export function useRlsMatrix() {
  const { token } = useSession();
  return useQuery({
    queryKey: ["rls-matrix"],
    queryFn: () => api.rlsMatrix(token as string),
    enabled: Boolean(token),
  });
}

export function useInventoryInsights(batchId: string | undefined, limit = 50) {
  const { token } = useSession();
  return useQuery({
    queryKey: ["inventory", batchId, limit],
    queryFn: () => api.inventoryInsights(token as string, batchId as string, limit),
    enabled: Boolean(token && batchId),
  });
}

export function useSupplierPortal() {
  const { token } = useSession();
  return useQuery({
    queryKey: ["supplier-portal"],
    queryFn: () => api.supplierPortal(token as string),
    enabled: Boolean(token),
  });
}

/** Run a pipeline stage and refresh everything that stage can change. */
export function useRunStage(batchId: string) {
  const { token } = useSession();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (stage: "profile" | "validate" | "clean" | "dry-run" | "load") =>
      api.runStage(token as string, batchId, stage),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["batch", batchId] });
      void client.invalidateQueries({ queryKey: ["batch-summary", batchId] });
      void client.invalidateQueries({ queryKey: ["findings-summary", batchId] });
      void client.invalidateQueries({ queryKey: ["health", batchId] });
      void client.invalidateQueries({ queryKey: ["reconciliation", batchId] });
      void client.invalidateQueries({ queryKey: ["batches"] });
    },
  });
}

export function useCreateBatch() {
  const { token } = useSession();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: BatchCreate) => api.createBatch(token as string, body),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["batches"] });
    },
  });
}

export function useRollback(batchId: string) {
  const { token } = useSession();
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.rollback(token as string, batchId),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["batch", batchId] });
      void client.invalidateQueries({ queryKey: ["batches"] });
      void client.invalidateQueries({ queryKey: ["audit"] });
    },
  });
}

export function useReviewDecision() {
  const { token } = useSession();
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({
      reviewId,
      decision,
      note,
    }: {
      reviewId: string;
      decision: "approve" | "reject";
      note?: string;
    }) => api.decideReview(token as string, reviewId, decision, note),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["review"] });
      void client.invalidateQueries({ queryKey: ["findings-summary"] });
    },
  });
}

export function useBulkApprove(batchId: string | undefined) {
  const { token } = useSession();
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({
      ids,
      note,
      decision = "approve",
    }: {
      ids: string[];
      note?: string;
      decision?: "approve" | "reject";
    }) => {
      if (!batchId) throw new Error("A batch is required to decide review items.");
      return api.bulkApprove(token as string, { batchId, decision, note, reviewIds: ids });
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["review"] });
      void client.invalidateQueries({ queryKey: ["findings-summary"] });
      void client.invalidateQueries({ queryKey: ["batch"] });
    },
  });
}
