"""Detection-rate measurement against the injected-defect manifest.

The generator labels every defect it injects. This module compares those labels
against what the rule engine actually reported, and answers three questions:

* recall    — of the defects we injected, how many did we catch?
* precision — of the findings we raised, how many correspond to a real defect?
* by type   — which defect types slip through?

That turns "we catch more than 95%" from a claim into a number a test asserts.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

# A defect type may be caught by more than one rule, so recall is measured on
# the (entity, record_id) pair rather than requiring an exact rule id match.
# This is deliberate: catching a bad GSTIN as "invalid format" or as
# "invalid tax id" both count, because the record is caught either way.
RECORD_LEVEL_TYPES = {
    "orphan",
    "uom_variant",
    "uom_unknown",
    "unbalanced_ledger",
    "missing_mandatory",
    "invalid_gstin",
    "invalid_pan",
    "invalid_email",
    "invalid_pincode",
    "invalid_phone",
    "stale_record",
    "whitespace_case",
    "date_format",
    "negative_stock",
    "impossible_date",
    "duplicate_exact",
    "duplicate_fuzzy",
    "duplicate_invoice",
    "retired_tax_code",
    "currency_mismatch",
    "negative_amount",
    "salary_outlier",
}


@dataclass
class TypeResult:
    defect_type: str
    injected: int
    detected: int
    missed_records: list[str] = field(default_factory=list)

    @property
    def recall(self) -> float:
        return self.detected / self.injected if self.injected else 0.0


@dataclass
class DetectionReport:
    total_injected: int
    total_detected: int
    total_findings: int
    true_positive_findings: int
    by_type: dict[str, TypeResult]
    undetected: list[dict[str, Any]]
    extra_findings: int
    column_level_findings: int = 0

    @property
    def recall(self) -> float:
        return self.total_detected / self.total_injected if self.total_injected else 0.0

    @property
    def precision(self) -> float:
        return (
            self.true_positive_findings / self.total_findings if self.total_findings else 0.0
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_injected": self.total_injected,
            "total_detected": self.total_detected,
            "recall": round(self.recall * 100, 2),
            "precision": round(self.precision * 100, 2),
            "total_findings": self.total_findings,
            "true_positive_findings": self.true_positive_findings,
            "extra_findings": self.extra_findings,
            "by_type": {
                name: {
                    "injected": r.injected,
                    "detected": r.detected,
                    "recall": round(r.recall * 100, 2),
                }
                for name, r in sorted(self.by_type.items())
            },
        }

    def summary_lines(self) -> list[str]:
        lines = [
            f"injected defects : {self.total_injected:,}",
            f"detected         : {self.total_detected:,}  ({self.recall * 100:.1f}%)",
            f"findings raised  : {self.total_findings:,}",
            f"precision        : {self.precision * 100:.1f}%",
            "",
            "by defect type:",
        ]
        for name, result in sorted(self.by_type.items(), key=lambda kv: kv[1].recall):
            bar = "#" * int(result.recall * 20)
            lines.append(
                f"  {name:20s} {result.detected:5d}/{result.injected:<5d} "
                f"{result.recall * 100:6.1f}%  {bar}"
            )
        return lines


def load_manifest(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Defect manifest not found at {path}")
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def measure(
    manifest: pd.DataFrame,
    findings: pd.DataFrame,
    *,
    ignore_column_level: bool = True,
) -> DetectionReport:
    """Compare the injected manifest against the findings the engine produced.

    Column-level findings (record ids beginning ``COLUMN:``) describe a whole
    column rather than one record, so they are excluded from precision by
    default: they cannot be attributed to a single injected defect.
    """
    if findings.empty:
        findings = pd.DataFrame(columns=["entity", "record_id", "rule_id", "field"])

    findings = findings.copy()
    findings["record_id"] = findings["record_id"].astype(str)
    findings["entity"] = findings["entity"].astype(str)

    scored = findings
    column_level_count = 0
    if ignore_column_level:
        is_column_level = findings["record_id"].str.startswith("COLUMN:")
        column_level_count = int(is_column_level.sum())
        scored = findings[~is_column_level]

    # A record counts as caught if any finding names it, regardless of rule id.
    detected_records: dict[str, set[str]] = defaultdict(set)
    for entity, record_id in zip(scored["entity"], scored["record_id"]):
        detected_records[entity].add(record_id)

    by_type: dict[str, TypeResult] = {}
    undetected: list[dict[str, Any]] = []

    for defect_type, group in manifest.groupby("defect_type"):
        injected = 0
        detected = 0
        missed: list[str] = []
        for _, row in group.iterrows():
            entity = str(row["entity"])
            record_id = str(row["record_id"])
            injected += 1
            if record_id in detected_records.get(entity, set()):
                detected += 1
            else:
                missed.append(record_id)
                if len(undetected) < 200:
                    undetected.append(
                        {
                            "defect_type": defect_type,
                            "entity": entity,
                            "record_id": record_id,
                            "field": row.get("field", ""),
                            "expected_rule": row.get("expected_rule", ""),
                        }
                    )
        by_type[str(defect_type)] = TypeResult(
            defect_type=str(defect_type),
            injected=injected,
            detected=detected,
            missed_records=missed[:50],
        )

    injected_pairs = set(
        zip(manifest["entity"].astype(str), manifest["record_id"].astype(str))
    )
    finding_pairs = list(zip(scored["entity"], scored["record_id"]))
    true_positives = sum(1 for pair in finding_pairs if pair in injected_pairs)

    return DetectionReport(
        total_injected=len(manifest),
        total_detected=sum(r.detected for r in by_type.values()),
        total_findings=len(scored),
        true_positive_findings=true_positives,
        by_type=by_type,
        undetected=undetected,
        extra_findings=len(scored) - true_positives,
        column_level_findings=column_level_count,
    )


def measure_from_files(
    *,
    manifest_path: Path,
    findings_path: Path,
) -> DetectionReport:
    manifest = load_manifest(manifest_path)
    findings = pd.read_csv(findings_path, dtype=str, keep_default_na=False)
    return measure(manifest, findings)
