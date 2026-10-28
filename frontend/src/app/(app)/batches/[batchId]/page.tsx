"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  Download,
  FileUp,
  Loader2,
  PlayCircle,
  RotateCcw,
  XCircle,
} from "lucide-react";

import { FlowChart } from "@/components/charts";
import { HealthGauge } from "@/components/health-gauge";
import { StageLegend, StageStepper } from "@/components/stage-stepper";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Progress } from "@/components/ui/progress";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import {
  useBatch,
  useBatchSummary,
  useHealth,
  useReconciliation,
  useRollback,
  useRunStage,
} from "@/lib/hooks";
import { useSession } from "@/lib/session";
import { formatDateTime, formatNumber, humanize } from "@/lib/utils";
import type { StageName } from "@/lib/types";

/** The stages a user can trigger by hand, in the order they must run. */
const RUNNABLE: {
  stage: "profile" | "validate" | "clean" | "dry-run" | "load";
  label: string;
  gate: StageName;
}[] = [
  { stage: "profile", label: "Profile", gate: "ingest" },
  { stage: "validate", label: "Validate", gate: "profile" },
  { stage: "clean", label: "Clean", gate: "validate" },
  { stage: "dry-run", label: "Dry run", gate: "clean" },
  { stage: "load", label: "Load", gate: "dry_run" },
];

export default function BatchDetailPage() {
  const params = useParams<{ batchId: string }>();
  const batchId = params.batchId;
  const { token, capabilities } = useSession();

  const { data: batch, isLoading } = useBatch(batchId);
  const { data: summary } = useBatchSummary(batchId);
  const { data: health } = useHealth(batchId);
  const { data: recon } = useReconciliation(batchId, "load");
  const runStage = useRunStage(batchId);
  const rollback = useRollback(batchId);

  const fileInput = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [lastRun, setLastRun] = useState<{
    stage: string;
    summary: Record<string, unknown>;
  } | null>(null);

  const stageStatus = useMemo(() => {
    const map = new Map<StageName, string>();
    batch?.stages.forEach((stage) => map.set(stage.stage, stage.status));
    return map;
  }, [batch]);

  async function upload(files: FileList | null) {
    if (!files?.length || !token) return;
    setUploading(true);
    setUploadError(null);
    try {
      // The API ingests one entity per request, because the entity decides the
      // target columns. The entity is read from the file name, which is the
      // convention the extracts follow: <entity>.csv.
      let last: { summary: Record<string, unknown> } | null = null;
      for (const file of Array.from(files)) {
        const entity = file.name.replace(/\.[^.]+$/, "").toLowerCase().replace(/[^a-z0-9_]/g, "_");
        last = await api.ingest(token, batchId, entity, file);
      }
      if (last) setLastRun({ stage: "ingest", summary: last.summary });
      window.location.reload();
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "The upload failed.");
    } finally {
      setUploading(false);
    }
  }

  async function run(stage: "profile" | "validate" | "clean" | "dry-run" | "load") {
    try {
      const result = await runStage.mutateAsync(stage);
      setLastRun({ stage, summary: result.summary });
    } catch {
      /* the mutation state carries the error */
    }
  }

  async function downloadReport() {
    if (!token || !batch) return;
    const blob = await api.reportCsv(token, batch.id);
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${batch.batch_code}-findings.csv`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-40" />
        <Skeleton className="h-64" />
      </div>
    );
  }

  if (!batch) {
    return (
      <EmptyState
        icon={AlertTriangle}
        title="Batch not found"
        description="It may have been deleted, or you may not have access to it."
        action={
          <Button asChild variant="outline">
            <Link href="/batches">Back to batches</Link>
          </Button>
        }
      />
    );
  }

  const ingested = stageStatus.get("ingest") === "succeeded";
  const canRun = capabilities?.can_validate ?? false;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <Button asChild variant="ghost" size="sm" className="-ml-2 mb-1">
            <Link href="/batches">
              <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
              All batches
            </Link>
          </Button>
          <h1 className="truncate text-xl font-semibold">{batch.name}</h1>
          <p className="font-mono text-xs text-muted-foreground">
            {batch.batch_code} · {batch.source_system} to {batch.target_system}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="outline">{humanize(batch.status)}</Badge>
          {health ? (
            <Badge variant={health.go_live_ready ? "ok" : "warn"}>
              {health.go_live_ready ? "Go-live ready" : "Not go-live ready"}
            </Badge>
          ) : null}
          {capabilities?.can_rollback ? (
            <Button
              variant="outline"
              size="sm"
              disabled={rollback.isPending}
              onClick={() => {
                if (
                  window.confirm(
                    "Roll back this batch? Every row this batch loaded will be removed from the target. The audit log keeps the record of what happened.",
                  )
                ) {
                  void rollback.mutateAsync();
                }
              }}
            >
              {rollback.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
              ) : (
                <RotateCcw className="h-3.5 w-3.5" aria-hidden />
              )}
              Roll back
            </Button>
          ) : null}
          <Button variant="outline" size="sm" onClick={() => void downloadReport()}>
            <Download className="h-3.5 w-3.5" aria-hidden />
            Export CSV
          </Button>
        </div>
      </div>

      {rollback.isError ? (
        <Alert variant="destructive">
          <XCircle className="h-4 w-4" aria-hidden />
          <AlertTitle>Rollback refused</AlertTitle>
          <AlertDescription>
            {rollback.error instanceof Error ? rollback.error.message : "The rollback failed."}
          </AlertDescription>
        </Alert>
      ) : null}

      {rollback.isSuccess ? (
        <Alert variant="success">
          <CheckCircle2 className="h-4 w-4" aria-hidden />
          <AlertTitle>Batch rolled back</AlertTitle>
          <AlertDescription>
            The rows this batch loaded have been removed. The batch and its findings remain, so the
            run is still auditable.
          </AlertDescription>
        </Alert>
      ) : null}

      <Card>
        <CardHeader className="pb-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <CardTitle>Pipeline</CardTitle>
            <StageLegend />
          </div>
        </CardHeader>
        <CardContent className="space-y-5">
          <StageStepper stages={batch.stages} />

          <Separator />

          <div className="flex flex-wrap items-center gap-2">
            <input
              ref={fileInput}
              type="file"
              multiple
              accept=".csv,.xlsx,.xls,.json"
              className="hidden"
              onChange={(event) => void upload(event.target.files)}
            />
            {capabilities?.can_ingest ? (
              <Button
                variant={ingested ? "outline" : "default"}
                size="sm"
                disabled={uploading}
                onClick={() => fileInput.current?.click()}
              >
                {uploading ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                ) : (
                  <FileUp className="h-3.5 w-3.5" aria-hidden />
                )}
                {ingested ? "Upload more files" : "Upload legacy files"}
              </Button>
            ) : null}

            {RUNNABLE.map(({ stage, label, gate }) => {
              const ready = stageStatus.get(gate) === "succeeded";
              return (
                <Button
                  key={stage}
                  variant="outline"
                  size="sm"
                  disabled={!canRun || !ready || runStage.isPending}
                  title={ready ? undefined : `Run ${gate} first`}
                  onClick={() => void run(stage)}
                >
                  {runStage.isPending && runStage.variables === stage ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                  ) : (
                    <PlayCircle className="h-3.5 w-3.5" aria-hidden />
                  )}
                  {label}
                </Button>
              );
            })}
          </div>

          {runStage.isPending ? (
            <div className="space-y-1.5">
              <p className="text-xs text-muted-foreground">
                Running {humanize(runStage.variables ?? "")}. Large stages take a moment.
              </p>
              <Progress value={65} />
            </div>
          ) : null}

          {uploadError ? (
            <Alert variant="destructive">
              <XCircle className="h-4 w-4" aria-hidden />
              <AlertTitle>Upload failed</AlertTitle>
              <AlertDescription>{uploadError}</AlertDescription>
            </Alert>
          ) : null}

          {runStage.isError ? (
            <Alert variant="destructive">
              <XCircle className="h-4 w-4" aria-hidden />
              <AlertTitle>The stage did not complete</AlertTitle>
              <AlertDescription>
                {runStage.error instanceof Error ? runStage.error.message : "Unknown error."}
              </AlertDescription>
            </Alert>
          ) : null}

          {lastRun ? (
            <Alert>
              <CheckCircle2 className="h-4 w-4" aria-hidden />
              <AlertTitle>{humanize(lastRun.stage)} finished</AlertTitle>
              <AlertDescription>
                <dl className="mt-1 grid gap-x-6 gap-y-0.5 sm:grid-cols-2">
                  {Object.entries(lastRun.summary)
                    .filter(([, value]) => typeof value !== "object")
                    .slice(0, 8)
                    .map(([key, value]) => (
                      <div key={key} className="flex justify-between gap-3">
                        <dt className="text-muted-foreground">{humanize(key)}</dt>
                        <dd className="tabular font-medium">{String(value)}</dd>
                      </div>
                    ))}
                </dl>
              </AlertDescription>
            </Alert>
          ) : null}
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>Health score</CardTitle>
            <CardDescription>After cleaning, against the score on arrival</CardDescription>
          </CardHeader>
          <CardContent>
            <HealthGauge
              score={health?.health_score ?? batch.health_score_after ?? null}
              before={batch.health_score_before ?? null}
            />
            {health ? (
              <p className="mt-3 text-center text-xs text-muted-foreground">
                {formatNumber(health.open_blockers)} open blockers
              </p>
            ) : null}
          </CardContent>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Records accounted for</CardTitle>
            <CardDescription>
              Every record is either loaded, archived or rejected. Nothing is lost silently.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {recon ? (
              <FlowChart counts={recon.counts} />
            ) : (
              <EmptyState
                icon={PlayCircle}
                title="No reconciliation yet"
                description="Run the dry run and the load to produce the in-and-out counts."
              />
            )}
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Findings</CardTitle>
            <CardDescription>
              {summary ? formatNumber(summary.findings.total) : "-"} in total
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {summary ? (
              <>
                {(["blocker", "warn", "info"] as const).map((severity) => {
                  const value = summary.findings.by_severity[severity] ?? 0;
                  const total = summary.findings.total || 1;
                  return (
                    <div key={severity} className="space-y-1">
                      <div className="flex items-center justify-between text-sm">
                        <span className="capitalize">{severity}</span>
                        <span className="tabular text-muted-foreground">{formatNumber(value)}</span>
                      </div>
                      <Progress
                        value={(value / total) * 100}
                        indicatorClassName={
                          severity === "blocker"
                            ? "bg-severity-blocker"
                            : severity === "warn"
                              ? "bg-severity-warn"
                              : "bg-severity-info"
                        }
                      />
                    </div>
                  );
                })}
                <Button asChild variant="outline" size="sm" className="w-full">
                  <Link href={`/quality?batch=${batchId}`}>Open the quality dashboard</Link>
                </Button>
              </>
            ) : (
              <p className="text-sm text-muted-foreground">Run validation to see findings.</p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Ingested files</CardTitle>
            <CardDescription>
              {batch.ingested.length
                ? `${batch.ingested.length} files, checksummed on arrival`
                : "Nothing uploaded yet"}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {batch.ingested.length ? (
              batch.ingested.map((file) => (
                <div
                  key={`${file.entity}-${file.file_name}`}
                  className="flex items-center justify-between gap-3 rounded-md border px-3 py-2 text-sm"
                >
                  <span className="min-w-0">
                    <span className="block truncate font-medium">{file.entity}</span>
                    <span className="block truncate text-xs text-muted-foreground">
                      {file.file_name}
                    </span>
                  </span>
                  <span className="shrink-0 text-right">
                    <span className="tabular block text-sm">{formatNumber(file.row_count)}</span>
                    <span className="block font-mono text-[10px] text-muted-foreground">
                      {file.checksum_sha256.slice(0, 10)}
                    </span>
                  </span>
                </div>
              ))
            ) : (
              <p className="text-sm text-muted-foreground">
                Upload CSV or Excel extracts for each entity to begin.
              </p>
            )}
          </CardContent>
        </Card>
      </div>

      {health ? (
        <Card>
          <CardHeader>
            <CardTitle>Go-live readiness</CardTitle>
            <CardDescription>
              The checklist the sign-off decision rests on. Each line is a fact, not a checkbox.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {health.checklist.map((item) => (
              <div key={item.item} className="flex items-start gap-2.5 text-sm">
                {item.passed ? (
                  <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-severity-ok" aria-hidden />
                ) : (
                  <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-severity-blocker" aria-hidden />
                )}
                <span>
                  <span className="font-medium">{humanize(item.item)}</span>
                  <span className="block text-muted-foreground">{item.detail}</span>
                </span>
              </div>
            ))}
            <p className="pt-1 text-xs text-muted-foreground">
              Last updated {formatDateTime(batch.completed_at ?? batch.created_at)}
            </p>
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}
