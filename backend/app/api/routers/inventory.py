"""Post-migration inventory insight.

This is what the cleaned data is for. Once items and stock are trustworthy, the
same numbers answer the question the business actually has: what is about to
run out, and what should we reorder.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_batch_or_404, parse_batch_id
from app.schemas.api import InventoryInsightOut, StockOut
from app.security.deps import CurrentUser, db, require

router = APIRouter(tags=["inventory"])


@router.get("/inventory/insights", response_model=InventoryInsightOut)
def insights(
    batch_id: str = Query(...),
    user: CurrentUser = Depends(require("inventory:read")),
    session: Session = Depends(db),
    limit: int = Query(default=50, ge=1, le=500),
) -> InventoryInsightOut:
    """Stock-out risk from the loaded, cleaned stock and item records.

    Risk is a ratio of what is on hand to what the item's reorder level says it
    should be, so it is comparable across items of very different sizes. The
    ordering puts the most urgent first, which is the order a buyer works in.
    """
    batch_id = parse_batch_id(batch_id)
    get_batch_or_404(session, batch_id)

    rows = session.execute(
        text(
            """
            select s.item_code,
                   i.name as item_name,
                   s.warehouse_code,
                   s.qty_on_hand::float as qty_on_hand,
                   i.reorder_level::float as reorder_level
              from public.inventory_stock s
              left join public.items i
                on i.item_code = s.item_code and i.batch_id = s.batch_id
             where s.batch_id = cast(:b as uuid)
            """
        ),
        {"b": batch_id},
    ).mappings().all()

    below: list[StockOut] = []
    at_risk = 0
    for row in rows:
        qty = float(row["qty_on_hand"] or 0)
        reorder = row["reorder_level"]
        risk = None
        if reorder is not None and float(reorder) > 0:
            ratio = qty / float(reorder)
            if ratio < 1:
                risk = "stock_out" if ratio < 0.25 else "low"
                at_risk += 1
            elif ratio < 1.5:
                risk = "watch"
            else:
                risk = "healthy"
        elif qty <= 0:
            risk = "stock_out"
            at_risk += 1
        else:
            risk = "unknown"

        below.append(
            StockOut(
                item_code=row["item_code"],
                item_name=row["item_name"],
                warehouse_code=row["warehouse_code"],
                qty_on_hand=qty,
                reorder_level=float(reorder) if reorder is not None else None,
                stock_out_risk=risk,
            )
        )

    rank = {"stock_out": 0, "low": 1, "watch": 2, "unknown": 3, "healthy": 4}
    below.sort(key=lambda s: (rank.get(s.stock_out_risk or "unknown", 9), s.item_code))

    return InventoryInsightOut(
        batch_id=batch_id,
        generated_at=datetime.now(UTC),
        total_items=len(below),
        at_risk=at_risk,
        below_reorder=below[:limit],
    )
