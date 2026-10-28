"""Reports and exports.

The migration report is the artefact a finance controller signs off and an
auditor reads afterwards, so it is available as JSON, CSV and a printable PDF.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_batch_or_404, jsonable, parse_batch_id, row_to_dict
from app.pipeline.load import latest_reconciliation, migration_health_score
from app.security.deps import CurrentUser, db, require

router = APIRouter(tags=["reports"])


def _batch_report(session: Session, batch_id: str) -> dict:
    """Assemble the full report from the stages that produced it.

    Every number here is read back from what the pipeline recorded, not
    recomputed. A report that recalculates can disagree with the run it claims
    to describe.
    """
    batch = get_batch_or_404(session, batch_id)

    stages = session.execute(
        text(
            "select stage, status, duration_ms, summary from public.migration_stages "
            "where batch_id = cast(:b as uuid) order by created_at"
        ),
        {"b": batch_id},
    ).mappings().all()

    findings = session.execute(
        text(
            """
            select severity::text as severity, count(*) as count
              from public.validation_findings
             where batch_id = cast(:b as uuid)
             group by severity
            """
        ),
        {"b": batch_id},
    ).all()

    by_entity = session.execute(
        text(
            """
            select entity, count(*) as findings,
                   count(*) filter (where severity = 'blocker') as blockers
              from public.validation_findings
             where batch_id = cast(:b as uuid)
             group by entity order by entity
            """
        ),
        {"b": batch_id},
    ).mappings().all()

    loaded = session.execute(
        text(
            """
            select 'categories' as entity, count(*) from public.categories where batch_id = cast(:b as uuid)
            union all select 'uom', count(*) from public.uom where batch_id = cast(:b as uuid)
            union all select 'tax_codes', count(*) from public.tax_codes where batch_id = cast(:b as uuid)
            union all select 'suppliers', count(*) from public.suppliers where batch_id = cast(:b as uuid)
            union all select 'customers', count(*) from public.customers where batch_id = cast(:b as uuid)
            union all select 'items', count(*) from public.items where batch_id = cast(:b as uuid)
            union all select 'chart_of_accounts', count(*) from public.chart_of_accounts where batch_id = cast(:b as uuid)
            union all select 'gl_balances', count(*) from public.gl_balances where batch_id = cast(:b as uuid)
            union all select 'purchase_orders', count(*) from public.purchase_orders where batch_id = cast(:b as uuid)
            union all select 'invoices', count(*) from public.invoices where batch_id = cast(:b as uuid)
            union all select 'inventory_stock', count(*) from public.inventory_stock where batch_id = cast(:b as uuid)
            union all select 'supplier_catalog', count(*) from public.supplier_catalog where batch_id = cast(:b as uuid)
            union all select 'employees_payroll_summary', count(*) from public.employees_payroll_summary where batch_id = cast(:b as uuid)
            """
        ),
        {"b": batch_id},
    ).all()

    review = session.execute(
        text(
            """
            select status::text as status, count(*) as count
              from public.review_queue
             where batch_id = cast(:b as uuid)
             group by status
            """
        ),
        {"b": batch_id},
    ).all()

    audit_count = session.execute(
        text("select count(*) from public.audit_log where batch_id = cast(:b as uuid)"),
        {"b": batch_id},
    ).scalar_one()

    try:
        dry = latest_reconciliation(session, batch_id, phase="dry_run")
    except LookupError:
        dry = None
    try:
        load = latest_reconciliation(session, batch_id, phase="load")
    except LookupError:
        load = None

    health = migration_health_score(session, batch_id)

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "batch": row_to_dict(batch),
        "health": health,
        "stages": [row_to_dict(s) for s in stages],
        "findings": {"by_severity": {r[0]: r[1] for r in findings}, "by_entity": [row_to_dict(r) for r in by_entity]},
        "loaded_rows": {r[0]: r[1] for r in loaded},
        "review_queue": {r[0]: r[1] for r in review},
        "reconciliation": {"dry_run": dry, "load": load},
        "audit_entries": int(audit_count),
    }


@router.get("/reports/{batch_id}")
def report(
    batch_id: str,
    user: CurrentUser = Depends(require("report:read")),
    session: Session = Depends(db),
) -> dict:
    batch_id = parse_batch_id(batch_id)
    return _batch_report(session, batch_id)


@router.get("/reports/{batch_id}/findings.csv")
def findings_csv(
    batch_id: str,
    user: CurrentUser = Depends(require("report:read")),
    session: Session = Depends(db),
) -> StreamingResponse:
    """Every finding as CSV, for the reviewer who wants a spreadsheet."""
    batch_id = parse_batch_id(batch_id)
    get_batch_or_404(session, batch_id)

    rows = session.execute(
        text(
            """
            select rule_id, rule_category, severity::text as severity, entity,
                   record_id, field, message, suggested_fix, auto_fixable,
                   status::text as status
              from public.validation_findings
             where batch_id = cast(:b as uuid)
             order by severity, entity, rule_id
            """
        ),
        {"b": batch_id},
    ).mappings().all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["rule_id", "category", "severity", "entity", "record_id", "field",
         "message", "suggested_fix", "auto_fixable", "status"]
    )
    for row in rows:
        writer.writerow(
            [
                row["rule_id"], row["rule_category"], row["severity"], row["entity"],
                row["record_id"], row["field"] or "", row["message"],
                row["suggested_fix"] or "", row["auto_fixable"], row["status"],
            ]
        )
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="findings-{batch_id[:8]}.csv"'
        },
    )


@router.get("/reports/{batch_id}/reconciliation.csv")
def reconciliation_csv(
    batch_id: str,
    user: CurrentUser = Depends(require("report:read")),
    session: Session = Depends(db),
) -> StreamingResponse:
    batch_id = parse_batch_id(batch_id)
    get_batch_or_404(session, batch_id)

    rows = session.execute(
        text(
            """
            select phase, per_entity, counts, balanced, created_at
              from public.reconciliation_reports
             where batch_id = cast(:b as uuid)
             order by created_at
            """
        ),
        {"b": batch_id},
    ).mappings().all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["phase", "entity", "rows_in", "rows_out", "rows_expected",
         "counts_match", "totals_match", "balanced"]
    )
    for row in rows:
        per_entity = row["per_entity"] or {}
        for entity, recon in sorted(per_entity.items()):
            writer.writerow(
                [
                    row["phase"], entity, recon.get("rows_in"), recon.get("rows_out"),
                    recon.get("rows_expected"), recon.get("counts_match"),
                    recon.get("totals_match"), recon.get("balanced"),
                ]
            )
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="reconciliation-{batch_id[:8]}.csv"'
        },
    )


@router.get("/reports/{batch_id}/report.pdf")
def report_pdf(
    batch_id: str,
    user: CurrentUser = Depends(require("report:read")),
    session: Session = Depends(db),
) -> Response:
    """The sign-off report as a PDF.

    The PDF is built from the same report dictionary the JSON endpoint returns,
    so the two cannot disagree. ReportLab is imported here rather than at module
    import so a deployment without it still serves every other endpoint.
    """
    batch_id = parse_batch_id(batch_id)
    data = _batch_report(session, batch_id)

    try:
        from app.services.pdf_report import build_migration_report
    except ImportError as exc:  # pragma: no cover - dependency missing
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="PDF export is not available: reportlab is not installed",
        ) from exc

    pdf = build_migration_report(data)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="migration-report-{batch_id[:8]}.pdf"'
        },
    )


@router.get("/reports/{batch_id}/audit.csv")
def audit_csv(
    batch_id: str,
    user: CurrentUser = Depends(require("report:export")),
    session: Session = Depends(db),
) -> StreamingResponse:
    batch_id = parse_batch_id(batch_id)
    get_batch_or_404(session, batch_id)

    rows = session.execute(
        text(
            """
            select occurred_at, actor_email, actor_role, action, entity, entity_id,
                   stage, request_id
              from public.audit_log
             where batch_id = cast(:b as uuid)
             order by occurred_at
            """
        ),
        {"b": batch_id},
    ).mappings().all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["occurred_at", "actor_email", "actor_role", "action", "entity", "entity_id",
         "stage", "request_id"]
    )
    for row in rows:
        writer.writerow(
            [
                row["occurred_at"].isoformat(), row["actor_email"], row["actor_role"],
                row["action"], row["entity"], row["entity_id"] or "",
                row["stage"] or "", row["request_id"] or "",
            ]
        )
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="audit-{batch_id[:8]}.csv"'},
    )
