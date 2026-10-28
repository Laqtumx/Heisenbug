"""Security introspection: prove the database enforces the access matrix.

The claim this project makes is that a supplier cannot see finance data because
Postgres refuses, not because the API remembered to filter. That claim is worth
demonstrating rather than asserting, so this router runs the same query under
each role's own claims and reports what the database returned.

The caller's identity is never trusted here. Every probe sets its own claims
inside its own transaction, so the results describe the policies, not the caller.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from sqlalchemy import text

from app.db.engine import rls_session, service_session
from app.schemas.api import RlsProbeOut, RlsMatrixOut
from app.security.deps import CurrentUser, require

router = APIRouter(prefix="/security", tags=["security"])

# One representative table per sensitivity class. A probe reads from each, which
# is what a policy is actually deciding.
PROBE_TABLES = [
    ("migration_batches", "pipeline", "Migration batches and stage history"),
    ("validation_findings", "pipeline", "Validation findings"),
    ("audit_log", "audit", "Immutable audit trail"),
    ("invoices", "finance", "Customer invoices"),
    ("gl_balances", "finance", "General ledger balances"),
    ("employees_payroll_summary", "payroll", "Payroll summary"),
    ("purchase_orders", "procurement", "Purchase orders"),
    ("items", "inventory", "Item master"),
    ("inventory_stock", "inventory", "Stock on hand"),
]

ROLES = [
    "admin",
    "migration_engineer",
    "finance_controller",
    "inventory_manager",
    "supplier",
]


def _claims_for(role: str, supplier_code: str | None = None) -> dict:
    claims: dict = {
        "sub": "00000000-0000-4000-8000-000000000000",
        "role": role,
        "app_metadata": {"role": role},
    }
    if supplier_code:
        claims["app_metadata"]["supplier_code"] = supplier_code
    return claims


def _baseline_counts() -> dict[str, int]:
    """How many rows each table holds when nothing is filtered.

    Read through the trusted session. Comparing a role's count against this is
    what separates "the policy hid the rows" from "the table is empty", which
    are very different outcomes that both present as zero.
    """
    counts: dict[str, int] = {}
    with service_session() as session:
        for table, _, _ in PROBE_TABLES:
            counts[table] = int(
                session.execute(text(f"select count(*) from public.{table}")).scalar()  # noqa: S608
            )
    return counts


def _default_supplier_code() -> str | None:
    """A supplier that actually has purchase orders.

    The external probe needs a supplier code, because that is what the policy
    matches on. Without one the probe reports zero for every table, which is
    correct for an unlinked account but makes the matrix look broken. Picking a
    supplier with orders shows what a real external account sees: its own rows
    and nothing else.
    """
    with service_session() as session:
        return session.execute(
            text(
                """
                select supplier_code
                  from public.purchase_orders
                 group by supplier_code
                 order by count(*) desc, supplier_code
                 limit 1
                """
            )
        ).scalar()


def _probe_role(
    role: str, supplier_code: str | None, baseline: dict[str, int]
) -> list[RlsProbeOut]:
    results: list[RlsProbeOut] = []
    with rls_session(_claims_for(role, supplier_code)) as session:
        for table, area, description in PROBE_TABLES:
            total = baseline.get(table, 0)
            try:
                count = int(
                    session.execute(
                        text(f"select count(*) from public.{table}")  # noqa: S608 - fixed allow-list
                    ).scalar()
                )
                results.append(
                    RlsProbeOut(
                        table=table,
                        area=area,
                        description=description,
                        visible=count > 0,
                        granted=True,
                        row_count=count,
                        total_rows=total,
                        denied_rows=max(total - count, 0),
                        denial_reason=None if count > 0 else "policy_filtered_all_rows",
                    )
                )
            except Exception as exc:
                # A policy can deny by returning no rows or by refusing the
                # query outright. Both are a denial, and the reason says which
                # mechanism did the work.
                session.rollback()
                results.append(
                    RlsProbeOut(
                        table=table,
                        area=area,
                        description=description,
                        visible=False,
                        granted=False,
                        row_count=None,
                        total_rows=total,
                        denied_rows=total,
                        denial_reason=type(exc).__name__,
                    )
                )
    return results


@router.get("/rls-matrix", response_model=RlsMatrixOut)
def rls_matrix(
    user: CurrentUser = Depends(require("audit:read")),
    supplier_code: str | None = Query(
        default=None,
        description="Supplier code to scope the external-supplier probe to.",
    ),
) -> RlsMatrixOut:
    """Run the same read as every role and report what the database returned.

    Reading this requires audit access, so the people who can see the matrix are
    the people already trusted with the log.
    """
    baseline = _baseline_counts()
    external_code = supplier_code or _default_supplier_code()
    roles: dict[str, list[RlsProbeOut]] = {}
    for role in ROLES:
        roles[role] = _probe_role(
            role, external_code if role == "supplier" else None, baseline
        )

    # The interesting comparisons: which roles can see each area.
    summary: dict[str, list[str]] = {}
    for table, area, _ in PROBE_TABLES:
        summary[table] = [
            role for role in ROLES if any(p.table == table and p.visible for p in roles[role])
        ]

    return RlsMatrixOut(
        roles=roles,
        summary=summary,
        enforced_by="postgres row level security, acting as the non-superuser role",
        supplier_code=external_code,
    )


@router.get("/rls-matrix/{table}")
def rls_table_matrix(
    table: str,
    user: CurrentUser = Depends(require("audit:read")),
    supplier_code: str | None = Query(default=None),
) -> dict:
    """One table, every role, so a single policy can be inspected on its own."""
    known = {name for name, _, _ in PROBE_TABLES}
    if table not in known:
        from fastapi import HTTPException, status

        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown table '{table}'. Probeable: {sorted(known)}",
        )

    baseline = _baseline_counts()
    external_code = supplier_code or _default_supplier_code()
    per_role = {}
    for role in ROLES:
        probes = _probe_role(
            role, external_code if role == "supplier" else None, baseline
        )
        match = next(p for p in probes if p.table == table)
        per_role[role] = match.model_dump()

    return {"table": table, "roles": per_role}
