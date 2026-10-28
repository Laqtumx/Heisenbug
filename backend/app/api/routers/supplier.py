"""The supplier portal.

This is the external, lowest-trust surface. Every read here goes through the
RLS session, so the filtering is done by Postgres from the claims in the token,
not by a WHERE clause this code remembered to write. If the policy were wrong,
this endpoint would leak; because the policy is in the database, it cannot.

The endpoints do not filter by supplier at all. That is the point.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.schemas.api import SupplierCatalogOut, SupplierPortalOut, SupplierPurchaseOrderOut
from app.security.deps import CurrentUser, get_current_user, rls_db

router = APIRouter(prefix="/supplier", tags=["supplier"])


def _require_supplier(user: CurrentUser) -> CurrentUser:
    """Only an external supplier account may use this surface.

    An internal user reading here would see every supplier's orders, because the
    internal policies allow it. Rather than special-case that, the portal is
    closed to internal roles; they have the full dashboards.
    """
    if not user.is_external:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "internal_role_not_permitted",
                "role": user.role,
                "detail": "The supplier portal is for external supplier accounts only.",
            },
        )
    if not user.supplier_code:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This supplier account is not linked to a supplier record",
        )
    return user


@router.get("/portal", response_model=SupplierPortalOut)
def portal(
    user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(rls_db),
) -> SupplierPortalOut:
    """The supplier's own purchase orders and catalogue.

    Note the queries. Neither one mentions a supplier code. Row Level Security
    supplies the filter from the token, so the only rows that can come back are
    this supplier's.
    """
    _require_supplier(user)

    orders = session.execute(
        text(
            """
            select po_number, supplier_code, order_date, currency, total_amount, status
              from public.purchase_orders
             order by order_date desc
            """
        )
    ).mappings().all()

    catalog = session.execute(
        text(
            """
            select supplier_code, item_code, supplier_sku, unit_price, currency,
                   lead_time_days, min_order_qty, is_active
              from public.supplier_catalog
             order by item_code
            """
        )
    ).mappings().all()

    order_rows = [SupplierPurchaseOrderOut(**dict(row)) for row in orders]
    return SupplierPortalOut(
        supplier_code=user.supplier_code or "",
        purchase_orders=order_rows,
        catalogue=[SupplierCatalogOut(**dict(row)) for row in catalog],
        purchase_order_count=len(order_rows),
        total_order_value=round(sum(float(o.total_amount or 0) for o in order_rows), 2),
        enforced_by="postgres row level security",
    )


@router.get("/purchase-orders", response_model=list[SupplierPurchaseOrderOut])
def purchase_orders(
    user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(rls_db),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[SupplierPurchaseOrderOut]:
    _require_supplier(user)
    rows = session.execute(
        text(
            """
            select po_number, supplier_code, order_date, currency, total_amount, status
              from public.purchase_orders
             order by order_date desc
             limit :limit
            """
        ),
        {"limit": limit},
    ).mappings().all()
    return [SupplierPurchaseOrderOut(**dict(row)) for row in rows]


@router.get("/catalogue", response_model=list[SupplierCatalogOut])
def catalogue(
    user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(rls_db),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[SupplierCatalogOut]:
    _require_supplier(user)
    rows = session.execute(
        text(
            """
            select supplier_code, item_code, supplier_sku, unit_price, currency,
                   lead_time_days, min_order_qty, is_active
              from public.supplier_catalog
             order by item_code
             limit :limit
            """
        ),
        {"limit": limit},
    ).mappings().all()
    return [SupplierCatalogOut(**dict(row)) for row in rows]
