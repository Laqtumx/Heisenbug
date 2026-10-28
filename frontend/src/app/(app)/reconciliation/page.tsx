"use client";

import { useSearchParams } from "next/navigation";
import { useState } from "react";
import { CheckCircle2, FileBarChart, XCircle } from "lucide-react";

import { FlowChart } from "@/components/charts";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useBatch, useReconciliation } from "@/lib/hooks";
import { formatDateTime, formatNumber, humanize } from "@/lib/utils";

/**
 * Reconciliation answers the question a finance controller actually asks: did
 * the money survive the move? Counts alone are not enough, so this screen leads
 * with the totals and shows counts underneath.
 */
export default function ReconciliationPage() {
  const search = useSearchParams();
  const batchId = search.get("batch") ?? undefined;
  const [phase, setPhase] = useState<"dry_run" | "load">("load");

  const { data: batch } = useBatch(batchId);
  const { data: recon, isLoading, error } = useReconciliation(batchId, phase);

  if (!batchId) {
    return (
      <EmptyState
        icon={FileBarChart}
        title="Pick a batch"
        description="Reconciliation reports on one batch. Open a batch and follow the link, or add ?batch=<id> to the address."
      />
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Reconciliation</h1>
          <p className="text-sm text-muted-foreground">
            {batch?.name ?? "Batch"} · what went in against what came out. The financial totals have
            to match to the paisa, or the load is not fit to sign off.
          </p>
        </div>
        <select
          className="h-9 rounded-md border border-input bg-card px-3 text-sm"
          value={phase}
          onChange={(event) => setPhase(event.target.value as "dry_run" | "load")}
        >
          <option value="load">After load</option>
          <option value="dry_run">Dry run</option>
        </select>
      </div>

      {isLoading ? (
        <Skeleton className="h-64" />
      ) : error || !recon ? (
        <EmptyState
          icon={FileBarChart}
          title="No reconciliation for this phase"
          description="Run the dry run or the load to produce one. Until then there is nothing to compare."
        />
      ) : (
        <>
          <Alert variant={recon.balanced ? "success" : "destructive"}>
            {recon.balanced ? (
              <CheckCircle2 className="h-4 w-4" aria-hidden />
            ) : (
              <XCircle className="h-4 w-4" aria-hidden />
            )}
            <AlertTitle>
              {recon.balanced
                ? "Reconciled. Every record is accounted for and the totals agree."
                : "Not reconciled. Counts or totals disagree, so this batch is not ready to load."}
            </AlertTitle>
            <AlertDescription>
              Compared at {formatDateTime(recon.created_at)} · phase {humanize(recon.phase)}
            </AlertDescription>
          </Alert>

          <div className="grid gap-4 lg:grid-cols-3">
            <Card>
              <CardHeader>
                <CardTitle>Records</CardTitle>
                <CardDescription>In, loaded, archived, rejected</CardDescription>
              </CardHeader>
              <CardContent className="space-y-2">
                {Object.entries(recon.counts).map(([key, value]) => (
                  <div key={key} className="flex items-center justify-between text-sm">
                    <span className="text-muted-foreground">{humanize(key)}</span>
                    <span className="tabular font-medium">
                      {typeof value === "boolean" ? (value ? "Yes" : "No") : formatNumber(value)}
                    </span>
                  </div>
                ))}
              </CardContent>
            </Card>

            <Card className="lg:col-span-2">
              <CardHeader>
                <CardTitle>Record flow</CardTitle>
              </CardHeader>
              <CardContent>
                <FlowChart counts={recon.counts} />
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader>
              <CardTitle>Financial totals, before and after</CardTitle>
              <CardDescription>
                The same measures taken on the source extract and on the target table. These must be
                identical, so any difference is a defect rather than a rounding note.
              </CardDescription>
            </CardHeader>
            <CardContent className="p-0">
              {Object.keys(recon.financial_totals).length ? (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Measure</TableHead>
                      <TableHead className="text-right">Source</TableHead>
                      <TableHead className="text-right">Target</TableHead>
                      <TableHead className="text-right">Difference</TableHead>
                      <TableHead className="text-right">Agrees</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {Object.entries(recon.financial_totals).flatMap(([measure, values]) =>
                      Object.keys({ ...values.in, ...values.out }).map((field) => {
                        const before = values.in[field] ?? 0;
                        const after = values.out[field] ?? 0;
                        const delta = Number(after) - Number(before);
                        const agrees = Math.abs(delta) < 0.01;
                        return (
                          <TableRow key={`${measure}-${field}`}>
                            <TableCell>
                              <span className="block text-sm font-medium">{humanize(measure)}</span>
                              <span className="block text-xs text-muted-foreground">{field}</span>
                            </TableCell>
                            <TableCell className="tabular text-right">
                              {formatNumber(Number(before))}
                            </TableCell>
                            <TableCell className="tabular text-right">
                              {formatNumber(Number(after))}
                            </TableCell>
                            <TableCell
                              className={`tabular text-right ${agrees ? "" : "font-medium text-destructive"}`}
                            >
                              {formatNumber(delta)}
                            </TableCell>
                            <TableCell className="text-right">
                              <Badge variant={agrees ? "ok" : "blocker"}>
                                {agrees ? "Yes" : "No"}
                              </Badge>
                            </TableCell>
                          </TableRow>
                        );
                      }),
                    )}
                  </TableBody>
                </Table>
              ) : (
                <p className="p-5 text-sm text-muted-foreground">
                  No financial measures recorded for this phase.
                </p>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Per entity</CardTitle>
              <CardDescription>
                Row counts and checksums for each entity, so a difference can be traced to the table
                it came from.
              </CardDescription>
            </CardHeader>
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Entity</TableHead>
                    <TableHead className="text-right">Rows in</TableHead>
                    <TableHead className="text-right">Rows out</TableHead>
                    <TableHead className="text-right">Expected</TableHead>
                    <TableHead>Counts</TableHead>
                    <TableHead>Checksum</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {Object.entries(recon.per_entity).map(([entity, row]) => (
                    <TableRow key={entity}>
                      <TableCell className="font-medium">{humanize(entity)}</TableCell>
                      <TableCell className="tabular text-right">
                        {formatNumber(row.rows_in)}
                      </TableCell>
                      <TableCell className="tabular text-right">
                        {formatNumber(row.rows_out)}
                      </TableCell>
                      <TableCell className="tabular text-right">
                        {formatNumber(row.rows_expected)}
                      </TableCell>
                      <TableCell>
                        <Badge variant={row.counts_match ? "ok" : "blocker"}>
                          {row.counts_match ? "Match" : "Mismatch"}
                        </Badge>
                      </TableCell>
                      <TableCell className="font-mono text-xs text-muted-foreground">
                        {recon.checksums[entity]?.slice(0, 16) ?? "-"}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
