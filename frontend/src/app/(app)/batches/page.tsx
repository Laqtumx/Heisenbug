"use client";

import Link from "next/link";
import { useState } from "react";
import { ArrowRight, Database, Plus } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useBatches, useCreateBatch } from "@/lib/hooks";
import { useSession } from "@/lib/session";
import { formatDateTime, formatNumber, humanize, scoreBand } from "@/lib/utils";

export default function BatchesPage() {
  const { capabilities } = useSession();
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [yearFrom, setYearFrom] = useState("2019");
  const [yearTo, setYearTo] = useState("2024");
  const [error, setError] = useState<string | null>(null);

  const { data: batches, isLoading } = useBatches({ limit: 50 });
  const createBatch = useCreateBatch();

  async function submit() {
    setError(null);
    if (!name.trim()) {
      setError("Give the batch a name.");
      return;
    }
    try {
      await createBatch.mutateAsync({
        name: name.trim(),
        fiscal_year_from: Number(yearFrom) || undefined,
        fiscal_year_to: Number(yearTo) || undefined,
      });
      setName("");
      setShowForm(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "The batch could not be created.");
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Migration batches</h1>
          <p className="text-sm text-muted-foreground">
            A batch is one migration run. Everything it does is tagged with its id, so any batch can
            be rolled back without touching the others.
          </p>
        </div>
        {capabilities?.can_manage_batches ? (
          <Button onClick={() => setShowForm((value) => !value)}>
            <Plus className="h-4 w-4" aria-hidden />
            New batch
          </Button>
        ) : null}
      </div>

      {showForm ? (
        <Card>
          <CardHeader>
            <CardTitle>New migration batch</CardTitle>
            <CardDescription>
              Name it after the cutover it belongs to, so the audit log reads clearly later.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-3">
              <div className="space-y-1.5 sm:col-span-3">
                <Label htmlFor="batch-name">Batch name</Label>
                <Input
                  id="batch-name"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  placeholder="FY24 cutover — legacy ERP"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="year-from">Fiscal year from</Label>
                <Input
                  id="year-from"
                  type="number"
                  value={yearFrom}
                  onChange={(event) => setYearFrom(event.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="year-to">Fiscal year to</Label>
                <Input
                  id="year-to"
                  type="number"
                  value={yearTo}
                  onChange={(event) => setYearTo(event.target.value)}
                />
              </div>
            </div>
            {error ? <p className="text-sm text-destructive">{error}</p> : null}
            <div className="flex gap-2">
              <Button onClick={() => void submit()} disabled={createBatch.isPending}>
                {createBatch.isPending ? "Creating…" : "Create batch"}
              </Button>
              <Button variant="outline" onClick={() => setShowForm(false)}>
                Cancel
              </Button>
            </div>
          </CardContent>
        </Card>
      ) : null}

      {isLoading ? (
        <Skeleton className="h-64" />
      ) : !batches?.length ? (
        <EmptyState
          icon={Database}
          title="No batches yet"
          description="Create a batch, then upload the legacy extracts for each entity."
        />
      ) : (
        <Card>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Batch</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Records in</TableHead>
                  <TableHead className="text-right">Loaded</TableHead>
                  <TableHead className="text-right">Archived</TableHead>
                  <TableHead className="text-right">Health</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {batches.map((batch) => {
                  const band = scoreBand(batch.health_score_after);
                  return (
                    <TableRow key={batch.id}>
                      <TableCell>
                        <span className="block font-medium">{batch.name}</span>
                        <span className="block font-mono text-xs text-muted-foreground">
                          {batch.batch_code}
                        </span>
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline">{humanize(batch.status)}</Badge>
                      </TableCell>
                      <TableCell className="tabular text-right">
                        {formatNumber(batch.record_count_in)}
                      </TableCell>
                      <TableCell className="tabular text-right">
                        {formatNumber(batch.record_count_loaded)}
                      </TableCell>
                      <TableCell className="tabular text-right">
                        {formatNumber(batch.record_count_archived)}
                      </TableCell>
                      <TableCell className="text-right">
                        <Badge variant={band.tone}>
                          {batch.health_score_after === null
                            ? "Not scored"
                            : batch.health_score_after.toFixed(1)}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {formatDateTime(batch.created_at)}
                      </TableCell>
                      <TableCell className="text-right">
                        <Button asChild variant="ghost" size="sm">
                          <Link href={`/batches/${batch.id}`}>
                            Open
                            <ArrowRight className="h-3.5 w-3.5" aria-hidden />
                          </Link>
                        </Button>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
