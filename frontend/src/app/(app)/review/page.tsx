"use client";

import { useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";
import {
  Check,
  ChevronLeft,
  ChevronRight,
  ClipboardCheck,
  Loader2,
  Sparkles,
  X,
} from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { SeverityBadge } from "@/components/ui/severity-badge";
import { useBulkApprove, useReviewDecision, useReviewQueue } from "@/lib/hooks";
import { cn, humanize } from "@/lib/utils";
import type { ReviewItem } from "@/lib/types";

const PAGE_SIZE = 20;

export default function ReviewPage() {
  const search = useSearchParams();
  const batchId = search.get("batch") ?? undefined;

  const [status, setStatus] = useState("pending");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [note, setNote] = useState("");

  const { data: queue, isLoading } = useReviewQueue({
    batch_id: batchId,
    status: status || undefined,
    limit: PAGE_SIZE,
    offset,
  });
  const decide = useReviewDecision();
  const bulk = useBulkApprove(batchId);

  const items = queue?.items ?? [];
  const total = queue?.total ?? 0;
  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const selectedIds = useMemo(() => Array.from(selected), [selected]);

  function toggle(id: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function decideOne(item: ReviewItem, decision: "approve" | "reject") {
    await decide.mutateAsync({ reviewId: item.id, decision, note });
    setSelected((current) => {
      const next = new Set(current);
      next.delete(item.id);
      return next;
    });
  }

  async function approveSelected() {
    if (!selectedIds.length) return;
    await bulk.mutateAsync({ ids: selectedIds, note });
    setSelected(new Set());
  }

  if (!batchId) {
    return (
      <EmptyState
        icon={ClipboardCheck}
        title="Pick a batch"
        description="The review queue works on one batch at a time. Open a batch and follow the link, or add ?batch=<id> to the address."
      />
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Review queue</h1>
          <p className="text-sm text-muted-foreground">
            The records the cleaner would not touch on its own. Each one shows the legacy value and
            the proposed value side by side, so the decision is made on evidence.
          </p>
        </div>
        <select
          className="h-9 rounded-md border border-input bg-card px-3 text-sm"
          value={status}
          onChange={(event) => {
            setStatus(event.target.value);
            setOffset(0);
            setSelected(new Set());
          }}
        >
          <option value="pending">Pending</option>
          <option value="approved">Approved</option>
          <option value="rejected">Rejected</option>
          <option value="">All statuses</option>
        </select>
      </div>

      {selectedIds.length ? (
        <Card className="border-primary/40 bg-accent/30">
          <CardContent className="flex flex-wrap items-center justify-between gap-3 p-4">
            <div>
              <p className="text-sm font-medium">{selectedIds.length} selected</p>
              <p className="text-xs text-muted-foreground">
                Approving accepts the proposed value for each. It is recorded against your account.
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <input
                className="h-9 w-56 rounded-md border border-input bg-card px-3 text-sm"
                placeholder="Note (optional)"
                value={note}
                onChange={(event) => setNote(event.target.value)}
              />
              <Button size="sm" disabled={bulk.isPending} onClick={() => void approveSelected()}>
                {bulk.isPending ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                ) : (
                  <Check className="h-3.5 w-3.5" aria-hidden />
                )}
                Approve selected
              </Button>
              <Button variant="outline" size="sm" onClick={() => setSelected(new Set())}>
                Clear
              </Button>
            </div>
          </CardContent>
        </Card>
      ) : null}

      {bulk.isSuccess ? (
        <Alert variant="success">
          <Check className="h-4 w-4" aria-hidden />
          <AlertTitle>
              {bulk.data.decided} of {bulk.data.requested} records {bulk.data.decision}d
            </AlertTitle>
          <AlertDescription>
            The decisions are in the audit log with your account against them.
          </AlertDescription>
        </Alert>
      ) : null}

      {decide.isError || bulk.isError ? (
        <Alert variant="destructive">
          <X className="h-4 w-4" aria-hidden />
          <AlertTitle>The decision was refused</AlertTitle>
          <AlertDescription>
            {(decide.error ?? bulk.error) instanceof Error
              ? ((decide.error ?? bulk.error) as Error).message
              : "Unknown error."}
          </AlertDescription>
        </Alert>
      ) : null}

      {isLoading ? (
        <div className="space-y-3">
          <Skeleton className="h-48" />
          <Skeleton className="h-48" />
        </div>
      ) : !items.length ? (
        <EmptyState
          icon={ClipboardCheck}
          title={status === "pending" ? "Nothing waiting" : "No records here"}
          description={
            status === "pending"
              ? "Every record the cleaner flagged has been decided. That is the state you want before go-live."
              : "Try a different status filter."
          }
        />
      ) : (
        <>
          <div className="space-y-3">
            {items.map((item) => (
              <ReviewCard
                key={item.id}
                item={item}
                selected={selected.has(item.id)}
                onToggle={() => toggle(item.id)}
                onDecide={(decision) => void decideOne(item, decision)}
                busy={decide.isPending && decide.variables?.reviewId === item.id}
              />
            ))}
          </div>

          <div className="flex items-center justify-between">
            <p className="text-xs text-muted-foreground">
              Page {page} of {pages} · {total} records
            </p>
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              >
                <ChevronLeft className="h-3.5 w-3.5" aria-hidden />
                Previous
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={offset + PAGE_SIZE >= total}
                onClick={() => setOffset(offset + PAGE_SIZE)}
              >
                Next
                <ChevronRight className="h-3.5 w-3.5" aria-hidden />
              </Button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

/** The fields that differ between the two versions, which is what matters. */
function differingFields(
  legacy: Record<string, string> | null,
  proposed: Record<string, string> | null,
): string[] {
  const keys = new Set([...Object.keys(legacy ?? {}), ...Object.keys(proposed ?? {})]);
  return Array.from(keys).filter((key) => (legacy?.[key] ?? "") !== (proposed?.[key] ?? ""));
}

function ReviewCard({
  item,
  selected,
  onToggle,
  onDecide,
  busy,
}: {
  item: ReviewItem;
  selected: boolean;
  onToggle: () => void;
  onDecide: (decision: "approve" | "reject") => void;
  busy: boolean;
}) {
  const [showAll, setShowAll] = useState(false);
  const changed = differingFields(item.legacy_record, item.proposed_record);
  const keys = showAll
    ? Array.from(
        new Set([...Object.keys(item.legacy_record ?? {}), ...Object.keys(item.proposed_record ?? {})]),
      )
    : changed.length
      ? changed
      : Object.keys(item.proposed_record ?? item.legacy_record ?? {});

  const decided = item.status !== "pending";

  return (
    <Card className={cn(selected && "border-primary/50")}>
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex items-start gap-3">
            {!decided ? (
              <input
                type="checkbox"
                checked={selected}
                onChange={onToggle}
                className="mt-1 h-4 w-4 rounded border-input"
                aria-label={`Select record ${item.record_id}`}
              />
            ) : null}
            <div>
              <CardTitle className="text-sm">
                {humanize(item.entity)} · <span className="font-mono">{item.record_id}</span>
              </CardTitle>
              <CardDescription className="mt-0.5">{item.reason}</CardDescription>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <SeverityBadge severity={item.severity} />
            {decided ? (
              <Badge variant={item.status === "rejected" ? "blocker" : "ok"}>
                {humanize(item.status)}
              </Badge>
            ) : null}
          </div>
        </div>
      </CardHeader>

      <CardContent className="space-y-4">
        {item.ai_explanation ? (
          <div className="rounded-md border border-primary/25 bg-accent/30 p-3">
            <p className="flex items-center gap-1.5 text-xs font-medium text-accent-foreground">
              <Sparkles className="h-3.5 w-3.5" aria-hidden />
              Suggested explanation
            </p>
            <p className="mt-1 text-sm">{item.ai_explanation}</p>
            {item.ai_suggested_fix ? (
              <p className="mt-1.5 font-mono text-xs text-muted-foreground">
                {JSON.stringify(item.ai_suggested_fix)}
              </p>
            ) : null}
            <p className="mt-1.5 text-[11px] text-muted-foreground">
              A suggestion from the model, not a decision. Approving is yours.
            </p>
          </div>
        ) : null}

        <div className="grid gap-3 md:grid-cols-2">
          <div className="rounded-md border">
            <p className="border-b bg-muted/50 px-3 py-1.5 text-xs font-medium">Legacy value</p>
            <dl className="divide-y">
              {keys.map((key) => (
                <div key={key} className="flex gap-3 px-3 py-1.5 text-sm">
                  <dt className="w-36 shrink-0 truncate text-muted-foreground">{key}</dt>
                  <dd className="min-w-0 break-words font-mono text-xs">
                    {item.legacy_record?.[key] ?? <span className="text-muted-foreground">-</span>}
                  </dd>
                </div>
              ))}
            </dl>
          </div>

          <div className="rounded-md border">
            <p className="border-b bg-muted/50 px-3 py-1.5 text-xs font-medium">Proposed value</p>
            <dl className="divide-y">
              {keys.map((key) => {
                const before = item.legacy_record?.[key] ?? "";
                const after = item.proposed_record?.[key] ?? "";
                const differs = before !== after;
                return (
                  <div key={key} className="flex gap-3 px-3 py-1.5 text-sm">
                    <dt className="w-36 shrink-0 truncate text-muted-foreground">{key}</dt>
                    <dd
                      className={cn(
                        "min-w-0 break-words font-mono text-xs",
                        differs && "font-medium text-primary",
                      )}
                    >
                      {item.proposed_record?.[key] ?? (
                        <span className="text-muted-foreground">-</span>
                      )}
                    </dd>
                  </div>
                );
              })}
            </dl>
          </div>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-2">
          <Button variant="ghost" size="sm" onClick={() => setShowAll((value) => !value)}>
            {showAll ? "Show only changed fields" : "Show all fields"}
          </Button>

          {decided ? (
            <p className="text-xs text-muted-foreground">
              {humanize(item.status)} by {item.decided_by ?? "unknown"}
              {item.decision_note ? ` · ${item.decision_note}` : ""}
            </p>
          ) : (
            <div className="flex gap-2">
              <Button size="sm" disabled={busy} onClick={() => onDecide("approve")}>
                {busy ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                ) : (
                  <Check className="h-3.5 w-3.5" aria-hidden />
                )}
                Approve
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={busy}
                onClick={() => onDecide("reject")}
              >
                <X className="h-3.5 w-3.5" aria-hidden />
                Reject
              </Button>
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
