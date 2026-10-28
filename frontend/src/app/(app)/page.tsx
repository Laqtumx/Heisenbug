"use client";

import Link from "next/link";
import { ArrowRight, Database, FileWarning, Gauge, Package, ScrollText, Sparkles } from "lucide-react";

import { HealthGauge } from "@/components/health-gauge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import { useBatches } from "@/lib/hooks";
import { useSession } from "@/lib/session";
import { formatDateTime, formatNumber, humanize } from "@/lib/utils";

export default function OverviewPage() {
  const { user, capabilities } = useSession();
  const { data: batches, isLoading, error } = useBatches({ limit: 10 });

  const latest = batches?.[0];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">
          {user ? `Welcome, ${user.full_name.split(" ")[0]}` : "Overview"}
        </h1>
        <p className="text-sm text-muted-foreground">
          Move the data, not the mistakes. Every stage below is recorded, reversible and audited.
        </p>
      </div>

      {isLoading ? (
        <div className="grid gap-4 md:grid-cols-3">
          <Skeleton className="h-40" />
          <Skeleton className="h-40" />
          <Skeleton className="h-40" />
        </div>
      ) : error ? (
        <EmptyState
          icon={FileWarning}
          title="Could not reach the API"
          description="Start the backend with make api, then reload. Signing in works, so this is the data endpoint failing."
        />
      ) : !batches?.length ? (
        <EmptyState
          icon={Database}
          title="No migration batches yet"
          description="Create a batch to begin. Upload the legacy extracts, and CleanSlate will profile, validate and repair them before anything reaches the target schema."
          action={
            <Button asChild>
              <Link href="/batches">Go to batches</Link>
            </Button>
          }
        />
      ) : (
        <>
          <div className="grid gap-4 lg:grid-cols-3">
            <Card className="lg:col-span-2">
              <CardHeader>
                <CardTitle>{latest?.name ?? "Latest batch"}</CardTitle>
                <CardDescription>
                  {latest?.batch_code} · created {formatDateTime(latest?.created_at)}
                </CardDescription>
              </CardHeader>
              <CardContent className="flex flex-wrap items-center justify-between gap-6">
                <HealthGauge
                  score={latest?.health_score_after ?? null}
                  before={latest?.health_score_before ?? null}
                />
                <dl className="grid flex-1 grid-cols-2 gap-4 text-sm sm:grid-cols-2">
                  <div>
                    <dt className="text-muted-foreground">Records in</dt>
                    <dd className="tabular text-lg font-semibold">
                      {formatNumber(latest?.record_count_in)}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">Loaded to target</dt>
                    <dd className="tabular text-lg font-semibold">
                      {formatNumber(latest?.record_count_loaded)}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">Archived, not migrated</dt>
                    <dd className="tabular text-lg font-semibold">
                      {formatNumber(latest?.record_count_archived)}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">Rejected</dt>
                    <dd className="tabular text-lg font-semibold">
                      {formatNumber(latest?.record_count_rejected)}
                    </dd>
                  </div>
                </dl>
                <div className="flex w-full gap-2 sm:w-auto">
                  <Button asChild variant="outline" size="sm">
                    <Link href={`/batches/${latest?.id}`}>
                      Open batch
                      <ArrowRight className="h-3.5 w-3.5" aria-hidden />
                    </Link>
                  </Button>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Your access</CardTitle>
                <CardDescription>
                  {user ? humanize(user.role) : ""} · decided by the database
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-2 text-sm">
                <CapabilityRow label="Run the pipeline" on={capabilities?.can_validate} />
                <CapabilityRow label="Approve reviews" on={capabilities?.can_review} />
                <CapabilityRow label="Load to target" on={capabilities?.can_load} />
                <CapabilityRow label="Finance dashboards" on={capabilities?.can_view_finance} />
                <CapabilityRow label="Payroll" on={capabilities?.can_view_payroll} />
                <CapabilityRow label="Inventory" on={capabilities?.can_view_inventory} />
                <CapabilityRow label="Audit log" on={capabilities?.can_view_audit} />
                <CapabilityRow label="Rollback" on={capabilities?.can_rollback} />
              </CardContent>
            </Card>
          </div>

          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <QuickLink
              href="/batches"
              icon={Database}
              title="Migration batches"
              description="Upload, profile, validate, clean, dry-run, load."
            />
            {capabilities?.can_review ? (
              <QuickLink
                href="/review"
                icon={Gauge}
                title="Review queue"
                description="Approve or reject the records the cleaner would not touch."
              />
            ) : null}
            {capabilities?.can_view_inventory ? (
              <QuickLink
                href="/inventory"
                icon={Package}
                title="Inventory insights"
                description="Stock-out risk and reorder suggestions, from cleaned data."
              />
            ) : null}
            {capabilities?.can_view_audit ? (
              <QuickLink
                href="/audit"
                icon={ScrollText}
                title="Audit log"
                description="Who changed what, with before and after, immutable."
              />
            ) : null}
            {capabilities?.can_view_finance ? (
              <QuickLink
                href="/finance"
                icon={Sparkles}
                title="Finance controls"
                description="Trial balance, tax codes, currency and outlier checks."
              />
            ) : null}
          </div>

          <Card>
            <CardHeader>
              <CardTitle>Recent batches</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {batches.slice(0, 5).map((batch) => (
                <Link
                  key={batch.id}
                  href={`/batches/${batch.id}`}
                  className="flex items-center justify-between gap-4 rounded-md border px-3 py-2 text-sm transition-colors hover:bg-accent"
                >
                  <span className="min-w-0">
                    <span className="block truncate font-medium">{batch.name}</span>
                    <span className="block text-xs text-muted-foreground">
                      {batch.batch_code} · {formatDateTime(batch.created_at)}
                    </span>
                  </span>
                  <span className="flex shrink-0 items-center gap-2">
                    <Badge variant="outline">{humanize(batch.status)}</Badge>
                    <span className="tabular text-xs text-muted-foreground">
                      {formatNumber(batch.record_count_loaded)} loaded
                    </span>
                  </span>
                </Link>
              ))}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}

function CapabilityRow({ label, on }: { label: string; on?: boolean }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-muted-foreground">{label}</span>
      <Badge variant={on ? "ok" : "secondary"}>{on ? "Allowed" : "Denied"}</Badge>
    </div>
  );
}

function QuickLink({
  href,
  icon: Icon,
  title,
  description,
}: {
  href: string;
  icon: typeof Database;
  title: string;
  description: string;
}) {
  return (
    <Link
      href={href}
      className="group rounded-lg border bg-card p-4 transition-colors hover:border-primary/40 hover:bg-accent/40"
    >
      <Icon className="h-4 w-4 text-primary" aria-hidden />
      <p className="mt-2 text-sm font-medium">{title}</p>
      <p className="mt-1 text-xs text-muted-foreground">{description}</p>
    </Link>
  );
}
