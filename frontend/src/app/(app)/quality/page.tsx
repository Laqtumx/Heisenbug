"use client";

import { useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";
import { Activity, ChevronLeft, ChevronRight, Filter } from "lucide-react";

import { EntityChart, SeverityChart } from "@/components/charts";
import { HealthGauge } from "@/components/health-gauge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { SeverityBadge } from "@/components/ui/severity-badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useBatch, useFindings, useFindingsSummary } from "@/lib/hooks";
import { formatDateTime, formatNumber, humanize } from "@/lib/utils";

const PAGE_SIZE = 25;

export default function QualityPage() {
  const search = useSearchParams();
  const batchId = search.get("batch") ?? undefined;

  const [severity, setSeverity] = useState("");
  const [entity, setEntity] = useState("");
  const [ruleId, setRuleId] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);

  const { data: batch } = useBatch(batchId);
  const { data: summary, isLoading: summaryLoading } = useFindingsSummary(batchId);
  const { data: findings, isLoading } = useFindings({
    batch_id: batchId,
    severity: severity || undefined,
    entity: entity || undefined,
    rule_id: ruleId || undefined,
    q: query || undefined,
    limit: PAGE_SIZE,
    offset,
  });

  const entities = useMemo(
    () => Object.keys(summary?.by_entity ?? {}).sort(),
    [summary?.by_entity],
  );
  const rules = useMemo(() => Object.keys(summary?.by_rule ?? {}).sort(), [summary?.by_rule]);

  if (!batchId) {
    return (
      <EmptyState
        icon={Activity}
        title="Pick a batch"
        description="The quality dashboard reports on one batch. Open a batch and follow the link, or add ?batch=<id> to the address."
      />
    );
  }

  const total = findings?.total ?? 0;
  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Data quality</h1>
        <p className="text-sm text-muted-foreground">
          {batch?.name ?? "Batch"} · every finding carries the rule that produced it, so a result can
          always be traced back to a decision.
        </p>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>Health score</CardTitle>
            <CardDescription>Higher is better. Blockers hold the score down.</CardDescription>
          </CardHeader>
          <CardContent>
            {summaryLoading ? (
              <Skeleton className="h-32" />
            ) : (
              <HealthGauge
                score={summary?.health_score ?? null}
                before={batch?.health_score_before ?? null}
              />
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>By severity</CardTitle>
            <CardDescription>{formatNumber(summary?.total ?? 0)} findings</CardDescription>
          </CardHeader>
          <CardContent>
            {summaryLoading ? (
              <Skeleton className="h-56" />
            ) : summary && Object.keys(summary.by_severity).length ? (
              <SeverityChart data={summary.by_severity} />
            ) : (
              <p className="py-12 text-center text-sm text-muted-foreground">
                No findings. Run validation.
              </p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>By entity</CardTitle>
            <CardDescription>Where the defects cluster</CardDescription>
          </CardHeader>
          <CardContent>
            {summaryLoading ? (
              <Skeleton className="h-56" />
            ) : summary && Object.keys(summary.by_entity).length ? (
              <EntityChart data={summary.by_entity} />
            ) : (
              <p className="py-12 text-center text-sm text-muted-foreground">
                No findings. Run validation.
              </p>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader className="pb-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <CardTitle>Findings</CardTitle>
            <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <Filter className="h-3 w-3" aria-hidden />
              {formatNumber(total)} matching
            </span>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            <Input
              placeholder="Search messages"
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                setOffset(0);
              }}
            />
            <select
              className="h-9 rounded-md border border-input bg-card px-3 text-sm"
              value={severity}
              onChange={(event) => {
                setSeverity(event.target.value);
                setOffset(0);
              }}
            >
              <option value="">All severities</option>
              <option value="blocker">Blocker</option>
              <option value="warn">Warning</option>
              <option value="info">Info</option>
            </select>
            <select
              className="h-9 rounded-md border border-input bg-card px-3 text-sm"
              value={entity}
              onChange={(event) => {
                setEntity(event.target.value);
                setOffset(0);
              }}
            >
              <option value="">All entities</option>
              {entities.map((name) => (
                <option key={name} value={name}>
                  {humanize(name)}
                </option>
              ))}
            </select>
            <select
              className="h-9 rounded-md border border-input bg-card px-3 text-sm"
              value={ruleId}
              onChange={(event) => {
                setRuleId(event.target.value);
                setOffset(0);
              }}
            >
              <option value="">All rules</option>
              {rules.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </div>

          {isLoading ? (
            <Skeleton className="h-64" />
          ) : !findings?.items.length ? (
            <EmptyState
              icon={Activity}
              title="No findings match"
              description="Either the batch is clean on these filters, or validation has not run yet."
            />
          ) : (
            <>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Severity</TableHead>
                    <TableHead>Rule</TableHead>
                    <TableHead>Entity</TableHead>
                    <TableHead>Record</TableHead>
                    <TableHead>Message</TableHead>
                    <TableHead>Fix</TableHead>
                    <TableHead>Detected</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {findings.items.map((finding) => (
                    <TableRow key={finding.id}>
                      <TableCell>
                        <SeverityBadge severity={finding.severity} />
                      </TableCell>
                      <TableCell className="font-mono text-xs">{finding.rule_id}</TableCell>
                      <TableCell className="text-xs">{humanize(finding.entity)}</TableCell>
                      <TableCell className="font-mono text-xs">{finding.record_id}</TableCell>
                      <TableCell className="max-w-md">
                        <span className="block text-sm">{finding.message}</span>
                        {finding.field ? (
                          <span className="block text-xs text-muted-foreground">
                            field: {finding.field}
                          </span>
                        ) : null}
                      </TableCell>
                      <TableCell>
                        {finding.suggested_fix ? (
                          <span className="flex items-start gap-1.5">
                            <Badge variant={finding.auto_fixable ? "ok" : "secondary"}>
                              {finding.auto_fixable ? "Automatic" : "Manual"}
                            </Badge>
                          </span>
                        ) : (
                          <span className="text-xs text-muted-foreground">None</span>
                        )}
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {formatDateTime(finding.detected_at)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>

              <div className="flex items-center justify-between">
                <p className="text-xs text-muted-foreground">
                  Page {page} of {pages}
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
        </CardContent>
      </Card>

      {summary ? (
        <Card>
          <CardHeader>
            <CardTitle>Findings by rule</CardTitle>
            <CardDescription>
              The rules that fired, most often first. A rule firing on everything usually means the
              rule is wrong, not the data.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-1.5">
            {Object.entries(summary.by_rule)
              .sort((a, b) => b[1] - a[1])
              .slice(0, 15)
              .map(([rule, count]) => (
                <div key={rule} className="flex items-center justify-between text-sm">
                  <span className="font-mono text-xs">{rule}</span>
                  <span className="tabular text-muted-foreground">{formatNumber(count)}</span>
                </div>
              ))}
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}
