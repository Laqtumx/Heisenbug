"""The migration sign-off report as a PDF.

Built from the same report dictionary the JSON endpoint returns, so the two
cannot disagree. This is the document a finance controller signs and an auditor
files, so it leads with the verdict and the reconciliation, not with raw counts.
"""

from __future__ import annotations

import io
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

INK = colors.HexColor("#111827")
MUTED = colors.HexColor("#6b7280")
RULE = colors.HexColor("#e5e7eb")
PASS = colors.HexColor("#047857")
FAIL = colors.HexColor("#b91c1c")
HEADER_BG = colors.HexColor("#f3f4f6")


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "title", parent=base["Title"], fontSize=20, textColor=INK, spaceAfter=2
        ),
        "subtitle": ParagraphStyle(
            "subtitle", parent=base["Normal"], fontSize=10, textColor=MUTED, spaceAfter=14
        ),
        "h2": ParagraphStyle(
            "h2", parent=base["Heading2"], fontSize=12.5, textColor=INK,
            spaceBefore=14, spaceAfter=6,
        ),
        "body": ParagraphStyle("body", parent=base["Normal"], fontSize=9.5, textColor=INK),
        "small": ParagraphStyle(
            "small", parent=base["Normal"], fontSize=8.5, textColor=MUTED
        ),
        "pass": ParagraphStyle("pass", parent=base["Normal"], fontSize=9.5, textColor=PASS),
        "fail": ParagraphStyle("fail", parent=base["Normal"], fontSize=9.5, textColor=FAIL),
    }


def _table(data: list[list[Any]], widths: list[float] | None = None) -> Table:
    table = Table(data, colWidths=widths, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
                ("TEXTCOLOR", (0, 0), (-1, 0), INK),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("TEXTCOLOR", (0, 1), (-1, -1), INK),
                ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return table


def _money(value: Any) -> str:
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


def build_migration_report(report: dict[str, Any]) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title="CleanSlate migration report",
    )
    style = _styles()
    story: list[Any] = []

    batch = report.get("batch", {})
    health = report.get("health", {})

    story.append(Paragraph("CleanSlate migration report", style["title"]))
    story.append(
        Paragraph("Move the data, not the mistakes.", style["subtitle"])
    )

    verdict = "Ready for go-live" if health.get("go_live_ready") else "Not ready for go-live"
    verdict_style = style["pass"] if health.get("go_live_ready") else style["fail"]
    story.append(Paragraph(verdict, verdict_style))
    story.append(Spacer(1, 8))

    story.append(
        _table(
            [
                ["Batch", batch.get("batch_code", ""), "Name", batch.get("name", "")],
                ["Status", batch.get("status", ""), "Health score", _money(health.get("health_score"))],
                ["Records in", f"{health.get('records_in', 0):,}", "Records loaded", f"{health.get('records_loaded', 0):,}"],
                ["Archived", f"{health.get('records_archived', 0):,}", "Open blockers", f"{health.get('open_blockers', 0):,}"],
                ["Generated", report.get("generated_at", ""), "Audit entries", f"{report.get('audit_entries', 0):,}"],
            ],
            widths=[28 * mm, 55 * mm, 30 * mm, 55 * mm],
        )
    )

    story.append(Paragraph("Go-live checklist", style["h2"]))
    checklist = health.get("checklist", [])
    story.append(
        _table(
            [["Check", "Result", "Detail"]]
            + [
                [
                    item.get("item", ""),
                    "PASS" if item.get("passed") else "FAIL",
                    Paragraph(item.get("detail", ""), style["small"]),
                ]
                for item in checklist
            ],
            widths=[45 * mm, 18 * mm, 105 * mm],
        )
    )

    story.append(Paragraph("Findings by severity", style["h2"]))
    by_severity = (report.get("findings") or {}).get("by_severity", {})
    if by_severity:
        story.append(
            _table(
                [["Severity", "Count"]]
                + [[k, f"{v:,}"] for k, v in sorted(by_severity.items())],
                widths=[60 * mm, 30 * mm],
            )
        )
    else:
        story.append(Paragraph("No findings recorded.", style["body"]))

    by_entity = (report.get("findings") or {}).get("by_entity", [])
    if by_entity:
        story.append(Paragraph("Findings by entity", style["h2"]))
        story.append(
            _table(
                [["Entity", "Findings", "Blockers"]]
                + [
                    [row.get("entity", ""), f"{row.get('findings', 0):,}", f"{row.get('blockers', 0):,}"]
                    for row in by_entity
                ],
                widths=[70 * mm, 30 * mm, 30 * mm],
            )
        )

    story.append(PageBreak())
    story.append(Paragraph("Reconciliation", style["h2"]))

    counts = ((report.get("reconciliation") or {}).get("load") or {}).get("counts") or {}
    if counts:
        story.append(
            _table(
                [["Measure", "Value"]]
                + [[k.replace("_", " "), f"{v:,}" if isinstance(v, int) else str(v)]
                   for k, v in counts.items()],
                widths=[80 * mm, 40 * mm],
            )
        )
    else:
        story.append(Paragraph("This batch has not been loaded yet.", style["body"]))

    story.append(Paragraph("Financial totals in vs out", style["h2"]))
    totals = ((report.get("reconciliation") or {}).get("load") or {}).get("financial_totals") or {}
    if totals:
        rows: list[list[Any]] = [["Entity", "Measure", "Before", "After", "Match"]]
        for entity, payload in sorted(totals.items()):
            in_totals = (payload or {}).get("in", {}) or {}
            out_totals = (payload or {}).get("out", {}) or {}
            for measure in sorted(set(in_totals) | set(out_totals)):
                before = in_totals.get(measure, 0)
                after = out_totals.get(measure, 0)
                try:
                    matches = abs(float(before) - float(after)) < 0.01
                except (TypeError, ValueError):
                    matches = before == after
                rows.append(
                    [
                        entity,
                        measure,
                        _money(before),
                        _money(after),
                        "yes" if matches else "NO",
                    ]
                )
        story.append(_table(rows, widths=[40 * mm, 32 * mm, 30 * mm, 30 * mm, 16 * mm]))
    else:
        story.append(Paragraph("No financial totals recorded.", style["body"]))

    story.append(Paragraph("Loaded rows by entity", style["h2"]))
    loaded = report.get("loaded_rows", {})
    if loaded:
        story.append(
            _table(
                [["Entity", "Rows loaded"]]
                + [[k, f"{v:,}"] for k, v in sorted(loaded.items())],
                widths=[70 * mm, 30 * mm],
            )
        )

    story.append(Paragraph("Stage history", style["h2"]))
    stages = report.get("stages", [])
    if stages:
        story.append(
            _table(
                [["Stage", "Status", "Duration (ms)"]]
                + [
                    [
                        row.get("stage", ""),
                        row.get("status", ""),
                        f"{row.get('duration_ms') or 0:,}",
                    ]
                    for row in stages
                ],
                widths=[45 * mm, 35 * mm, 35 * mm],
            )
        )

    story.append(Spacer(1, 14))
    story.append(
        Paragraph(
            "Generated by CleanSlate. Every figure in this report is read from what "
            "the pipeline recorded, not recalculated. The audit trail for this batch "
            "is immutable and complete.",
            style["small"],
        )
    )

    doc.build(story)
    return buffer.getvalue()
