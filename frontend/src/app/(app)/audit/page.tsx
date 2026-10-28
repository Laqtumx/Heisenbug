"use client";

import { Fragment, useState } from "react";
import { ChevronLeft, ChevronRight, Lock, ScrollText, ShieldCheck, XCircle } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
import { useAudit, useAuditImmutability } from "@/lib/hooks";
import { formatDateTime, formatNumber, humanize } from "@/lib/utils";

const PAGE_SIZE = 30;

export default function AuditPage() {
  const [action, setAction] = useState("");
  const [entity, setEntity] = useState("");
  const [offset, setOffset] = useState(0);
  const [expanded, setExpanded] = useState<number | null>(null);

  const { data: audit, isLoading } = useAudit({
    action: action || undefined,
    entity: entity || undefined,
    limit: PAGE_SIZE,
    offset,
  });
  const immutability = useAuditImmutability();

  const total = audit?.total ?? 0;
  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Audit log</h1>
        <p className="text-sm text-muted-foreground">
          Who did what, when, with the state before and after. Written by the database, not by the
          application, and append-only.
        </p>
      </div>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-sm">
            <ShieldCheck className="h-4 w-4 text-primary" aria-hidden />
            Immutability, demonstrated rather than asserted
          </CardTitle>
          <CardDescription>
            This asks the database whether the triggers are in place, then tries to change and
            delete a row and reports whether each attempt was refused.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {immutability.isLoading ? (
            <Skeleton className="h-16" />
          ) : immutability.data ? (
            <div className="grid gap-3 sm:grid-cols-4">
              <Proof
                label="Triggers installed"
                passed={immutability.data.triggers.length > 0}
              />
              {immutability.data.attempts.map((attempt) => (
                <Proof
                  key={attempt.operation}
                  label={`${attempt.operation} refused`}
                  passed={attempt.blocked}
                />
              ))}
              <Proof label="Log immutable" passed={immutability.data.immutable} />
              <p className="text-xs text-muted-foreground sm:col-span-4">
                {immutability.data.table} carries{" "}
                {immutability.data.triggers.join(" and ") || "no triggers"}. The API ran a real
                UPDATE and a real DELETE and the database refused both, so this is a measurement
                rather than a claim.
              </p>
            </div>
          ) : (
            <Alert variant="warning">
              <XCircle className="h-4 w-4" aria-hidden />
              <AlertTitle>Could not check</AlertTitle>
              <AlertDescription>
                {immutability.error instanceof Error
                  ? immutability.error.message
                  : "The check did not return a result."}
              </AlertDescription>
            </Alert>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <CardTitle>Entries</CardTitle>
            <div className="flex flex-wrap gap-2">
              <input
                className="h-9 w-44 rounded-md border border-input bg-card px-3 text-sm"
                placeholder="Filter by action"
                value={action}
                onChange={(event) => {
                  setAction(event.target.value);
                  setOffset(0);
                }}
              />
              <input
                className="h-9 w-44 rounded-md border border-input bg-card px-3 text-sm"
                placeholder="Filter by entity"
                value={entity}
                onChange={(event) => {
                  setEntity(event.target.value);
                  setOffset(0);
                }}
              />
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <Skeleton className="h-64" />
          ) : !audit?.items.length ? (
            <EmptyState
              icon={ScrollText}
              title="No audit entries"
              description="Nothing has happened yet, or the filters exclude everything."
            />
          ) : (
            <>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>When</TableHead>
                    <TableHead>Actor</TableHead>
                    <TableHead>Action</TableHead>
                    <TableHead>Entity</TableHead>
                    <TableHead>Batch</TableHead>
                    <TableHead />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {audit.items.map((entry) => (
                    <Fragment key={entry.id}>
                      <TableRow>
                        <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                          {formatDateTime(entry.occurred_at)}
                        </TableCell>
                        <TableCell>
                          <span className="block text-sm">{entry.actor_email}</span>
                          <span className="block text-xs text-muted-foreground">
                            {humanize(entry.actor_role)}
                          </span>
                        </TableCell>
                        <TableCell>
                          <Badge variant="outline" className="font-mono text-[11px]">
                            {entry.action}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-sm">
                          {humanize(entry.entity)}
                          {entry.entity_id ? (
                            <span className="block font-mono text-xs text-muted-foreground">
                              {entry.entity_id}
                            </span>
                          ) : null}
                        </TableCell>
                        <TableCell className="font-mono text-xs text-muted-foreground">
                          {entry.batch_id?.slice(0, 8) ?? "-"}
                        </TableCell>
                        <TableCell className="text-right">
                          {entry.before || entry.after ? (
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => setExpanded(expanded === entry.id ? null : entry.id)}
                            >
                              {expanded === entry.id ? "Hide" : "Before / after"}
                            </Button>
                          ) : null}
                        </TableCell>
                      </TableRow>
                      {expanded === entry.id ? (
                        <TableRow>
                          <TableCell colSpan={6} className="bg-muted/40">
                            <div className="grid gap-3 py-2 md:grid-cols-2">
                              <div>
                                <p className="mb-1 text-xs font-medium text-muted-foreground">
                                  Before
                                </p>
                                <pre className="max-h-56 overflow-auto rounded-md border bg-card p-3 font-mono text-xs scrollbar-thin">
                                  {JSON.stringify(entry.before, null, 2) ?? "null"}
                                </pre>
                              </div>
                              <div>
                                <p className="mb-1 text-xs font-medium text-muted-foreground">
                                  After
                                </p>
                                <pre className="max-h-56 overflow-auto rounded-md border bg-card p-3 font-mono text-xs scrollbar-thin">
                                  {JSON.stringify(entry.after, null, 2) ?? "null"}
                                </pre>
                              </div>
                            </div>
                          </TableCell>
                        </TableRow>
                      ) : null}
                    </Fragment>
                  ))}
                </TableBody>
              </Table>

              <div className="mt-4 flex items-center justify-between">
                <p className="text-xs text-muted-foreground">
                  Page {page} of {pages} · {formatNumber(total)} entries
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
    </div>
  );
}

function Proof({ label, passed }: { label: string; passed: boolean }) {
  return (
    <div className="flex items-center gap-2 rounded-md border px-3 py-2">
      {passed ? (
        <Lock className="h-3.5 w-3.5 shrink-0 text-severity-ok" aria-hidden />
      ) : (
        <XCircle className="h-3.5 w-3.5 shrink-0 text-severity-blocker" aria-hidden />
      )}
      <span className="text-sm">{label}</span>
      <Badge variant={passed ? "ok" : "blocker"} className="ml-auto">
        {passed ? "Yes" : "No"}
      </Badge>
    </div>
  );
}
