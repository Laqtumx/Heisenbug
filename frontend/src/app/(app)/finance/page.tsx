"use client";

import { useSearchParams } from "next/navigation";
import { useState } from "react";
import { AlertTriangle, CheckCircle2, ScrollText, Sparkles, XCircle } from "lucide-react";

import { RoleGate } from "@/components/role-gate";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useBatches, useFindings, useFindingsSummary, useRules } from "@/lib/hooks";
import { formatNumber, humanize } from "@/lib/utils";

/** The rule categories that belong to finance, matched on the rule's own category. */
const FINANCE_CATEGORIES = new Set([
  "finance",
  "ledger",
  "tax",
  "currency",
  "payroll",
  "financial",
]);

export default function FinancePage() {
  const search = useSearchParams();
  const initialBatch = search.get("batch") ?? undefined;
  const { data: batches } = useBatches({ limit: 20 });
  const [batchId, setBatchId] = useState<string | undefined>(initialBatch);

  const activeBatch = batchId ?? batches?.[0]?.id;
  const { data: summary, isLoading } = useFindingsSummary(activeBatch);
  const { data: rules } = useRules();

  // The finance findings, read through the same endpoint as any other. What
  // makes this a finance view is the filter and the permission behind it.
  const { data: financeFindings } = useFindings({
    batch_id: activeBatch,
    severity: "blocker",
    limit: 50,
  });

  const financeRules = (rules ?? []).filter((rule) =>
    FINANCE_CATEGORIES.has(rule.category.toLowerCase()),
  );

  const severityRows = Object.entries(summary?.by_severity ?? {});

  return (
    <RoleGate capability="can_view_finance">
      <div className="space-y-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold">Finance controls</h1>
            <p className="text-sm text-muted-foreground">
              The checks a controller signs off against: the ledger balances, the tax codes are
              live, the currencies agree, and no outliers slipped through.
            </p>
          </div>
          {batches?.length ? (
            <select
              className="h-9 rounded-md border border-input bg-card px-3 text-sm"
              value={activeBatch ?? ""}
              onChange={(event) => setBatchId(event.target.value)}
            >
              {batches.map((batch) => (
                <option key={batch.id} value={batch.id}>
                  {batch.name}
                </option>
              ))}
            </select>
          ) : null}
        </div>

        <Alert>
          <ScrollText className="h-4 w-4" aria-hidden />
          <AlertTitle>This view is not hidden, it is refused</AlertTitle>
          <AlertDescription>
            Roles without finance access get a 403 from the API for every request this page makes.
            Hiding the menu item is a convenience; the refusal is what protects the data.
          </AlertDescription>
        </Alert>

        {isLoading ? (
          <Skeleton className="h-64" />
        ) : !activeBatch ? (
          <EmptyState
            icon={Sparkles}
            title="No batch selected"
            description="Load a batch to run the finance checks against it."
          />
        ) : (
          <Tabs defaultValue="checks">
            <TabsList>
              <TabsTrigger value="checks">Control checks</TabsTrigger>
              <TabsTrigger value="blockers">Open blockers</TabsTrigger>
              <TabsTrigger value="rules">Finance rules</TabsTrigger>
            </TabsList>

            <TabsContent value="checks" className="space-y-4">
              <div className="grid gap-4 sm:grid-cols-3">
                <Card>
                  <CardHeader className="pb-2">
                    <CardDescription>Findings in this batch</CardDescription>
                    <CardTitle className="tabular text-2xl">
                      {formatNumber(summary?.total ?? 0)}
                    </CardTitle>
                  </CardHeader>
                </Card>
                <Card>
                  <CardHeader className="pb-2">
                    <CardDescription>Blockers</CardDescription>
                    <CardTitle className="tabular text-2xl text-severity-blocker">
                      {formatNumber(summary?.by_severity.blocker ?? 0)}
                    </CardTitle>
                  </CardHeader>
                </Card>
                <Card>
                  <CardHeader className="pb-2">
                    <CardDescription>Finance rules active</CardDescription>
                    <CardTitle className="tabular text-2xl">{financeRules.length}</CardTitle>
                  </CardHeader>
                </Card>
              </div>

              <Card>
                <CardHeader>
                  <CardTitle>The controls that run</CardTitle>
                  <CardDescription>
                    Each rule is configuration, not code, so a controller can read what will be
                    checked before it runs.
                  </CardDescription>
                </CardHeader>
                <CardContent className="p-0">
                  {financeRules.length ? (
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Rule</TableHead>
                          <TableHead>Category</TableHead>
                          <TableHead>Severity</TableHead>
                          <TableHead>Auto-fixable</TableHead>
                          <TableHead>What it checks</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {financeRules.map((rule) => (
                          <TableRow key={rule.rule_id}>
                            <TableCell>
                              <span className="block text-sm font-medium">{rule.name}</span>
                              <span className="block font-mono text-xs text-muted-foreground">
                                {rule.rule_id}
                              </span>
                            </TableCell>
                            <TableCell>
                              <Badge variant="outline">{humanize(rule.category)}</Badge>
                            </TableCell>
                            <TableCell>
                              <SeverityBadge severity={rule.severity} />
                            </TableCell>
                            <TableCell>
                              <Badge variant={rule.auto_fixable ? "ok" : "secondary"}>
                                {rule.auto_fixable ? "Yes" : "No"}
                              </Badge>
                            </TableCell>
                            <TableCell className="text-sm text-muted-foreground">
                              {rule.message}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  ) : (
                    <p className="p-5 text-sm text-muted-foreground">
                      No finance rules are registered. Check the rule configuration.
                    </p>
                  )}
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="blockers">
              <Card>
                <CardHeader>
                  <CardTitle>Open blockers</CardTitle>
                  <CardDescription>
                    These hold the batch back from sign-off. Resolve them, or record why they are
                    acceptable.
                  </CardDescription>
                </CardHeader>
                <CardContent className="p-0">
                  {financeFindings?.items.length ? (
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Severity</TableHead>
                          <TableHead>Entity</TableHead>
                          <TableHead>Record</TableHead>
                          <TableHead>Message</TableHead>
                          <TableHead>Suggested fix</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {financeFindings.items.map((finding) => (
                          <TableRow key={finding.id}>
                            <TableCell>
                              <SeverityBadge severity={finding.severity} />
                            </TableCell>
                            <TableCell className="text-xs">{humanize(finding.entity)}</TableCell>
                            <TableCell className="font-mono text-xs">
                              {finding.record_id}
                            </TableCell>
                            <TableCell className="max-w-md text-sm">{finding.message}</TableCell>
                            <TableCell className="max-w-xs text-xs text-muted-foreground">
                              {finding.suggested_fix ?? "-"}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  ) : (
                    <div className="flex items-center gap-2 p-5 text-sm text-severity-ok">
                      <CheckCircle2 className="h-4 w-4" aria-hidden />
                      No blockers in this batch.
                    </div>
                  )}
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="rules">
              <Card>
                <CardHeader>
                  <CardTitle>Findings by severity</CardTitle>
                  <CardDescription>
                    The shape of the problem before anyone starts fixing it.
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-2">
                  {severityRows.length ? (
                    severityRows.map(([severity, count]) => (
                      <div key={severity} className="flex items-center justify-between text-sm">
                        <span className="flex items-center gap-2">
                          <SeverityBadge severity={severity} />
                        </span>
                        <span className="tabular text-muted-foreground">
                          {formatNumber(count)}
                        </span>
                      </div>
                    ))
                  ) : (
                    <p className="text-sm text-muted-foreground">No findings recorded.</p>
                  )}
                </CardContent>
              </Card>
            </TabsContent>
          </Tabs>
        )}

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-sm">
              <AlertTriangle className="h-4 w-4 text-severity-warn" aria-hidden />
              What the controller is being asked to accept
            </CardTitle>
            <CardDescription>
              A trial balance that does not balance, a retired tax code still in use, or a salary
              outside its band are all things that look fine in a row count and expensive after
              go-live. These rules exist to catch them before the load, not after.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2 text-xs">
            <Badge variant="outline">
              <XCircle className="mr-1 h-3 w-3" aria-hidden />
              Debits against credits
            </Badge>
            <Badge variant="outline">
              <XCircle className="mr-1 h-3 w-3" aria-hidden />
              Retired tax codes
            </Badge>
            <Badge variant="outline">
              <XCircle className="mr-1 h-3 w-3" aria-hidden />
              Currency mismatches
            </Badge>
            <Badge variant="outline">
              <XCircle className="mr-1 h-3 w-3" aria-hidden />
              Duplicate invoice numbers
            </Badge>
            <Badge variant="outline">
              <XCircle className="mr-1 h-3 w-3" aria-hidden />
              Negative stock
            </Badge>
            <Badge variant="outline">
              <XCircle className="mr-1 h-3 w-3" aria-hidden />
              Impossible dates
            </Badge>
            <Badge variant="outline">
              <XCircle className="mr-1 h-3 w-3" aria-hidden />
              Salary outliers
            </Badge>
          </CardContent>
        </Card>
      </div>
    </RoleGate>
  );
}
