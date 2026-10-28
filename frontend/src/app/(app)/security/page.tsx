"use client";

import { ShieldCheck, Table2 } from "lucide-react";

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
import { useRlsMatrix } from "@/lib/hooks";
import { formatNumber, humanize } from "@/lib/utils";

const ROLES = [
  "admin",
  "migration_engineer",
  "finance_controller",
  "inventory_manager",
  "supplier",
] as const;

/**
 * The access matrix, measured rather than drawn.
 *
 * Every cell is the result of a real query run under that role's own claims.
 * A tick means rows came back; a cross means the policy returned none. The
 * baseline column makes the difference visible: a role can be denied by an
 * empty table or by a policy, and only the second is enforcement.
 */
export default function SecurityPage() {
  const { data, isLoading, error } = useRlsMatrix();

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-56" />
        <Skeleton className="h-64" />
      </div>
    );
  }

  if (error || !data) {
    return (
      <EmptyState
        icon={ShieldCheck}
        title="The matrix could not be read"
        description="This screen needs audit access. If you are signed in as a role without it, the API refuses the request, which is the correct behaviour."
      />
    );
  }

  // One row per table, one column per role, plus the unfiltered baseline.
  const tables = data.roles.admin.map((probe) => probe.table);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Access matrix</h1>
        <p className="text-sm text-muted-foreground">
          Measured, not drawn. Each cell is a real query run under that role&apos;s own claims, so what
          you see is what Postgres decided.
        </p>
      </div>

      <Alert>
        <ShieldCheck className="h-4 w-4" aria-hidden />
        <AlertTitle>Enforced by {data.enforced_by}</AlertTitle>
        <AlertDescription>
          The API never filters these rows by role. Postgres reads the role from the verified token
          and applies the policy, so a mistake in the API cannot leak data.
          {data.supplier_code
            ? ` The supplier column is measured as ${data.supplier_code}, which holds purchase orders, so its zeroes are denials rather than an unlinked account.`
            : ""}
        </AlertDescription>
      </Alert>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Table2 className="h-4 w-4" aria-hidden />
            Who can read what
          </CardTitle>
          <CardDescription>
            Each cell is the number of rows that came back. The baseline column is the same table
            read without any policy, so a zero against a non-zero baseline means the policy hid the
            rows rather than the table being empty.
          </CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Table</TableHead>
                <TableHead>Area</TableHead>
                <TableHead className="text-right">Baseline</TableHead>
                {ROLES.map((role) => (
                  <TableHead key={role} className="text-center">
                    {humanize(role)}
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {tables.map((table) => {
                const baseline = data.roles.admin.find((probe) => probe.table === table);
                return (
                  <TableRow key={table}>
                    <TableCell>
                      <span className="block font-mono text-xs">{table}</span>
                      <span className="block text-xs text-muted-foreground">
                        {baseline?.description}
                      </span>
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline">{humanize(baseline?.area ?? "")}</Badge>
                    </TableCell>
                    <TableCell className="tabular text-right text-xs text-muted-foreground">
                      {formatNumber(baseline?.total_rows ?? 0)}
                    </TableCell>
                    {ROLES.map((role) => {
                      const probe = data.roles[role]?.find((item) => item.table === table);
                      const visible = probe?.visible ?? false;
                      return (
                        <TableCell key={role} className="text-center">
                          {visible ? (
                            <Badge variant="ok" title={`${probe?.row_count} rows visible`}>
                              {formatNumber(probe?.row_count ?? 0)}
                            </Badge>
                          ) : (
                            <Badge
                              variant="blocker"
                              title={
                                probe?.denial_reason === "policy_filtered_all_rows"
                                  ? `All ${formatNumber(probe.denied_rows)} rows hidden by the policy`
                                  : "No grant for this role"
                              }
                            >
                              0
                            </Badge>
                          )}
                        </TableCell>
                      );
                    })}
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        {ROLES.map((role) => {
          const probes = data.roles[role] ?? [];
          const visible = probes.filter((probe) => probe.visible);
          const denied = probes.filter((probe) => !probe.visible && probe.denied_rows > 0);
          return (
            <Card key={role}>
              <CardHeader className="pb-3">
                <CardTitle className="text-sm">{humanize(role)}</CardTitle>
                <CardDescription>
                  {visible.length} of {probes.length} tables readable
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-2 text-sm">
                <p>
                  <span className="text-muted-foreground">Can read: </span>
                  {visible.length ? visible.map((probe) => probe.table).join(", ") : "nothing"}
                </p>
                {denied.length ? (
                  <p>
                    <span className="text-muted-foreground">Denied despite holding rows: </span>
                    {denied
                      .map((probe) => `${probe.table} (${formatNumber(probe.denied_rows)})`)
                      .join(", ")}
                  </p>
                ) : null}
              </CardContent>
            </Card>
          );
        })}
      </div>
    </div>
  );
}
