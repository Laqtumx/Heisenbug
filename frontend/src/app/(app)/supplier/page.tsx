"use client";

import { Lock, Package, Truck } from "lucide-react";

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
import { useSupplierPortal } from "@/lib/hooks";
import { useSession } from "@/lib/session";
import { formatCurrency, formatNumber } from "@/lib/utils";

/**
 * The external supplier view.
 *
 * This page has no filter of its own. It asks for purchase orders and shows
 * what comes back. What comes back is only this supplier's rows, because the
 * policy in the database matched them against the claim in the token.
 */
export default function SupplierPage() {
  const { user } = useSession();
  const { data, isLoading, error } = useSupplierPortal();

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-56" />
        <Skeleton className="h-40" />
        <Skeleton className="h-64" />
      </div>
    );
  }

  if (error || !data) {
    return (
      <EmptyState
        icon={Truck}
        title="The portal could not be loaded"
        description={
          error instanceof Error
            ? error.message
            : "This page is for external supplier accounts. An internal role has the full dashboards instead."
        }
      />
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">My orders</h1>
        <p className="text-sm text-muted-foreground">
          {user?.full_name} · supplier {data.supplier_code}
        </p>
      </div>

      <Alert>
        <Lock className="h-4 w-4" aria-hidden />
        <AlertTitle>You are seeing your own records only</AlertTitle>
        <AlertDescription>
          This page does not filter by supplier. {data.enforced_by} does, using the supplier code in
          your signed-in token. Requests for other suppliers&apos; data, or for finance data, are
          refused by the database itself.
        </AlertDescription>
      </Alert>

      <div className="grid gap-4 sm:grid-cols-3">
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Purchase orders</CardDescription>
            <CardTitle className="tabular text-2xl">
              {formatNumber(data.purchase_order_count)}
            </CardTitle>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Total order value</CardDescription>
            <CardTitle className="tabular text-2xl">
              {formatCurrency(data.total_order_value)}
            </CardTitle>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Catalogue lines</CardDescription>
            <CardTitle className="tabular text-2xl">{formatNumber(data.catalogue.length)}</CardTitle>
          </CardHeader>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Purchase orders</CardTitle>
          <CardDescription>Newest first</CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          {data.purchase_orders.length ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>PO number</TableHead>
                  <TableHead>Order date</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Total</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.purchase_orders.map((order) => (
                  <TableRow key={order.po_number}>
                    <TableCell className="font-mono text-xs">{order.po_number}</TableCell>
                    <TableCell className="text-sm">{order.order_date ?? "-"}</TableCell>
                    <TableCell>
                      <Badge variant="outline">{order.status ?? "unknown"}</Badge>
                    </TableCell>
                    <TableCell className="tabular text-right">
                      {order.total_amount
                        ? formatCurrency(Number(order.total_amount), order.currency ?? "INR")
                        : "-"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <EmptyState
              icon={Truck}
              title="No purchase orders"
              description="Your account is linked to a supplier with no orders in the loaded data."
              className="m-5"
            />
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Package className="h-4 w-4" aria-hidden />
            My catalogue
          </CardTitle>
          <CardDescription>The items you supply, with the agreed prices</CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          {data.catalogue.length ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Item</TableHead>
                  <TableHead>Your SKU</TableHead>
                  <TableHead className="text-right">Unit price</TableHead>
                  <TableHead className="text-right">Lead time</TableHead>
                  <TableHead className="text-right">Min order</TableHead>
                  <TableHead>Status</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.catalogue.map((item) => (
                  <TableRow key={`${item.item_code}-${item.supplier_sku}`}>
                    <TableCell className="font-mono text-xs">{item.item_code}</TableCell>
                    <TableCell className="font-mono text-xs">{item.supplier_sku ?? "-"}</TableCell>
                    <TableCell className="tabular text-right">
                      {item.unit_price
                        ? formatCurrency(Number(item.unit_price), item.currency ?? "INR")
                        : "-"}
                    </TableCell>
                    <TableCell className="tabular text-right">
                      {item.lead_time_days !== null ? `${item.lead_time_days} days` : "-"}
                    </TableCell>
                    <TableCell className="tabular text-right">
                      {item.min_order_qty ?? "-"}
                    </TableCell>
                    <TableCell>
                      <Badge variant={item.is_active ? "ok" : "secondary"}>
                        {item.is_active ? "Active" : "Inactive"}
                      </Badge>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <EmptyState
              icon={Package}
              title="No catalogue lines"
              description="No items are recorded against your supplier account."
              className="m-5"
            />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
