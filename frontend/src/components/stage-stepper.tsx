"use client";

import { Check, Circle, Loader2, X } from "lucide-react";

import { cn } from "@/lib/utils";
import type { StageName, StageStatus } from "@/lib/types";

const ORDER: { key: StageName; label: string; description: string }[] = [
  { key: "ingest", label: "Upload", description: "Files land raw in Mongo, a batch is created" },
  { key: "profile", label: "Profile", description: "Nulls, distinct counts, formats, outliers" },
  { key: "validate", label: "Validate", description: "Rules run and produce findings" },
  { key: "clean", label: "Clean", description: "Safe fixes applied, the rest queued for review" },
  { key: "map", label: "Map", description: "Legacy columns matched to the target schema" },
  { key: "dry_run", label: "Dry run", description: "Staged load, totals reconciled before commit" },
  { key: "load", label: "Load", description: "Transactional load in dependency order" },
  { key: "verify", label: "Sign off", description: "Post-load reconciliation and health score" },
];

export function StageStepper({
  stages,
  current,
}: {
  stages: { stage: StageName; status: StageStatus; duration_ms: number | null }[];
  current?: StageName;
}) {
  const byStage = new Map(stages.map((stage) => [stage.stage, stage]));

  return (
    <ol className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
      {ORDER.map((step, index) => {
        const state = byStage.get(step.key);
        const status: StageStatus = state?.status ?? "pending";
        const active = current === step.key || status === "running";

        return (
          <li
            key={step.key}
            className={cn(
              "flex items-start gap-2.5 rounded-lg border p-3",
              active && "border-primary/50 bg-accent/40",
              status === "failed" && "border-destructive/40 bg-destructive/5",
              status === "succeeded" && "border-severity-ok/30",
            )}
          >
            <span
              className={cn(
                "mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-medium",
                status === "succeeded" && "bg-severity-ok/15 text-severity-ok",
                status === "running" && "bg-primary/15 text-primary",
                status === "failed" && "bg-destructive/15 text-destructive",
                status === "pending" && "bg-muted text-muted-foreground",
                status === "skipped" && "bg-muted text-muted-foreground",
              )}
            >
              {status === "succeeded" ? (
                <Check className="h-3 w-3" aria-hidden />
              ) : status === "running" ? (
                <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
              ) : status === "failed" ? (
                <X className="h-3 w-3" aria-hidden />
              ) : (
                index + 1
              )}
            </span>
            <span className="min-w-0">
              <span className="flex items-center gap-2">
                <span className="text-sm font-medium">{step.label}</span>
                {state?.duration_ms ? (
                  <span className="tabular text-[11px] text-muted-foreground">
                    {(state.duration_ms / 1000).toFixed(1)}s
                  </span>
                ) : null}
              </span>
              <span className="mt-0.5 block text-xs text-muted-foreground">{step.description}</span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}

export function StageLegend() {
  return (
    <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
      <span className="flex items-center gap-1.5">
        <Check className="h-3 w-3 text-severity-ok" aria-hidden />
        Complete
      </span>
      <span className="flex items-center gap-1.5">
        <Loader2 className="h-3 w-3" aria-hidden />
        Running
      </span>
      <span className="flex items-center gap-1.5">
        <Circle className="h-3 w-3" aria-hidden />
        Not started
      </span>
      <span className="flex items-center gap-1.5">
        <X className="h-3 w-3 text-destructive" aria-hidden />
        Failed
      </span>
    </div>
  );
}
