"use client";

import { useSearchParams } from "next/navigation";
import { useState } from "react";
import { CheckCircle2, Package, Sparkles, TrendingDown } from "lucide-react";

import { RoleGate } from "@/components/role-gate";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useBatches, useInventoryInsights } from "@/lib/hooks";
import { formatNumber } from "@/lib/utils";

/**
 * Stock-out risk, computed on cleaned data.
 *
 * The point of showing this after a migration is that the numbers are only
 * worth acting on because the data behind them was validated. A reorder
 * suggestion built on duplicate items and bad quantities is worse than none.
 */
export default function InventoryPage() {
  const search = useSearchParams();
  const initialBatch = search.get("batch") ?? undefined;
  const { data: batches } = useBatches({ limit: 20 });
  const [batchId, setBatchId] = useState<string | undefined>(initialBatch);

  const activeBatch = batchId ?? batches?.[0]?.id;
  const { data, isLoading } = useInventoryInsights(activeBatch, 100);

  return (
    <RoleGate capability="can_view_inventory">
      <div className="space-y-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold">Inventory insights</h1>
            <p className="text-sm text-muted-foreground">
              Stock-out risk and reorder suggestions, built on the cleaned data rather than the raw
              extract.
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

        {isLoading ? (
          <Skeleton className="h-64" />
        ) : !data ? (
          <EmptyState
            icon={Package}
            title="No inventory data"
            description="Load a batch that includes items and stock to see this view."
          />
        ) : (
          <>
            <div className="grid gap-4 sm:grid-cols-3">
              <Card>
                <CardHeader className="pb-2">
                  <CardDescription>Stock lines</CardDescription>
                  <CardTitle className="tabular text-2xl">
                    {formatNumber(data.total_items)}
                  </CardTitle>
                </CardHeader>
              </Card>
              <Card>
                <CardHeader className="pb-2">
                  <CardDescription>Below reorder level</CardDescription>
                  <CardTitle className="tabular text-2xl text-severity-warn">
                    {formatNumber(data.at_risk)}
                  </CardTitle>
                </CardHeader>
              </Card>
              <Card>
                <CardHeader className="pb-2">
                  <CardDescription>Proportion at risk</CardDescription>
                  <CardTitle className="tabular text-2xl">
                    {data.total_items
                      ? `${((data.at_risk / data.total_items) * 100).toFixed(1)}%`
                      : "0%"}
                  </CardTitle>
                </CardHeader>
              </Card>
            </div>

            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <TrendingDown className="h-4 w-4 text-severity-warn" aria-hidden />
                  Reorder suggestions
                </CardTitle>
                <CardDescription>
                  Lines at or below their reorder level, worst first. Quantities come from the
                  cleaned stock table.
                </CardDescription>
              </CardHeader>
              <CardContent className="p-0">
                {data.below_reorder.length ? (
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Item</TableHead>
                        <TableHead>Warehouse</TableHead>
                        <TableHead className="text-right">On hand</TableHead>
                        <TableHead className="text-right">Reorder level</TableHead>
                        <TableHead className="w-40">Cover</TableHead>
                        <TableHead>Risk</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {data.below_reorder.map((row) => {
                        const level = row.reorder_level ?? 0;
                        const cover = level > 0 ? Math.min((row.qty_on_hand / level) * 100, 100) : 0;
                        return (
                          <TableRow key={`${row.item_code}-${row.warehouse_code}`}>
                            <TableCell>
                              <span className="block font-mono text-xs">{row.item_code}</span>
                              {row.item_name ? (
                                <span className="block text-xs text-muted-foreground">
                                  {row.item_name}
                                </span>
                              ) : null}
                            </TableCell>
                            <TableCell className="text-sm">{row.warehouse_code}</TableCell>
                            <TableCell className="tabular text-right">
                              {formatNumber(row.qty_on_hand)}
                            </TableCell>
                            <TableCell className="tabular text-right">
                              {formatNumber(level)}
                            </TableCell>
                            <TableCell>
                              <Progress
                                value={cover}
                                indicatorClassName={
                                  cover < 50 ? "bg-severity-blocker" : "bg-severity-warn"
                                }
                              />
                            </TableCell>
                            <TableCell>
                              <Badge variant={cover < 50 ? "blocker" : "warn"}>
                                {cover < 50 ? "High" : "Moderate"}
                              </Badge>
                            </TableCell>
                          </TableRow>
                        );
                      })}
                    </TableBody>
                  </Table>
                ) : (
                  <div className="p-5">
                    <div className="flex items-center gap-2 text-sm text-severity-ok">
                      <CheckCircle2 className="h-4 w-4" aria-hidden />
                      Every stock line is above its reorder level.
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-sm">
                  <Sparkles className="h-4 w-4 text-primary" aria-hidden />
                  Why this is trustworthy
                </CardTitle>
                <CardDescription>
                  These suggestions are computed on the target tables, after cleaning. Mixed units,
                  duplicate items and impossible quantities were resolved before the load, so a
                  reorder decision is not built on a bad number.
                </CardDescription>
              </CardHeader>
            </Card>
          </>
        )}
      </div>
    </RoleGate>
  );
}
