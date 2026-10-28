"""Stage 2 — PROFILE.

Per column: null rate, distinct count, inferred type, observed format patterns,
outliers and date ranges. Everything is vectorised Pandas; no Python loop over
rows, so 100k rows stay well inside the 60 second budget.

The profile is what tells the rule engine which checks are worth running: a
column that is 100% null cannot produce an invalid-format finding, and a column
holding 4,000 distinct values is an identifier rather than a category.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.pipeline.entities import ENTITIES_BY_NAME
from app.security import audit

logger = logging.getLogger(__name__)

# Values that mean "empty" in a legacy export but are not literally null.
NULL_TOKENS = {
    "", " ", "null", "NULL", "Null", "none", "NONE", "None", "n/a", "N/A", "NA",
    "nan", "NaN", "-", "--", "?", "??", "#N/A", "#REF!", "0/0/0000", "0000-00-00",
}

_INT_RE = re.compile(r"^-?\d+$")
_FLOAT_RE = re.compile(r"^-?\d+(\.\d+)?$")
_BOOL_RE = re.compile(r"^(true|false|yes|no|y|n|0|1)$", re.IGNORECASE)
_DATE_PATTERNS: list[tuple[str, str]] = [
    ("%Y-%m-%d", "ISO 8601 (YYYY-MM-DD)"),
    ("%d/%m/%Y", "DD/MM/YYYY"),
    ("%m/%d/%Y", "MM/DD/YYYY"),
    ("%d-%b-%Y", "DD-Mon-YYYY"),
    ("%d.%m.%Y", "DD.MM.YYYY"),
    ("%Y%m%d", "YYYYMMDD"),
    ("%Y-%m-%dT%H:%M:%S", "ISO 8601 timestamp"),
    ("%d/%m/%Y %H:%M", "DD/MM/YYYY HH:MM"),
]
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
_GSTIN_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
_PAN_RE = re.compile(r"^[A-Z]{5}\d{4}[A-Z]$")


@dataclass
class ColumnProfile:
    entity: str
    column_name: str
    row_count: int
    null_count: int
    null_pct: float
    distinct_count: int
    inferred_type: str
    format_patterns: list[dict[str, Any]] = field(default_factory=list)
    min_value: str | None = None
    max_value: str | None = None
    mean_value: float | None = None
    stddev_value: float | None = None
    outlier_count: int = 0
    sample_values: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class ProfileSummary:
    batch_id: str
    entities: dict[str, list[ColumnProfile]]
    total_columns: int
    total_rows: int
    duration_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "total_columns": self.total_columns,
            "total_rows": self.total_rows,
            "duration_ms": self.duration_ms,
            "entities": {
                entity: [p.__dict__ for p in profiles]
                for entity, profiles in self.entities.items()
            },
        }


def _is_null_series(series: pd.Series) -> pd.Series:
    """Boolean mask of values that mean empty, including legacy null tokens."""
    stripped = series.str.strip()
    return stripped.isin(NULL_TOKENS) | series.isna()


def _date_format_breakdown(series: pd.Series) -> tuple[dict[str, float], float]:
    """Share of values matching each known date format.

    Returns (shares, combined_share). A column where no single format dominates
    but the union of formats covers almost everything is a mixed-format date
    column — exactly the defect a legacy export produces and the profiler should
    surface rather than hide behind a generic 'string' label.
    """
    sample = series.head(20_000)
    if sample.empty:
        return {}, 0.0

    shares: dict[str, float] = {}
    covered = pd.Series(False, index=sample.index)
    for fmt, label in _DATE_PATTERNS:
        try:
            parsed = pd.to_datetime(sample, format=fmt, errors="coerce")
        except (ValueError, TypeError):
            continue
        mask = parsed.notna()
        share = float(mask.mean())
        if share > 0:
            shares[label] = round(share, 4)
            covered |= mask
    return shares, float(covered.mean())


def _infer_type(series: pd.Series, non_null: pd.Series) -> str:
    if non_null.empty:
        return "empty"

    sample = non_null.head(5000)
    n = len(sample)
    lowered = sample.str.lower()

    bool_hits = lowered.str.match(_BOOL_RE).sum()
    if bool_hits / n > 0.98 and sample.nunique() <= 4:
        return "bool"

    int_hits = sample.str.match(_INT_RE).sum()
    if int_hits / n > 0.98:
        return "int"

    float_hits = sample.str.match(_FLOAT_RE).sum()
    if float_hits / n > 0.98:
        return "float"

    for fmt, _ in _DATE_PATTERNS:
        try:
            parsed = pd.to_datetime(sample, format=fmt, errors="coerce")
        except (ValueError, TypeError):
            continue
        if parsed.notna().mean() > 0.95:
            return "date"

    # No single format dominates. If several formats together cover the column,
    # it is a mixed-format date column, which is a finding in its own right.
    shares, combined = _date_format_breakdown(sample)
    if combined > 0.95 and len(shares) > 1:
        return "date_mixed"

    email_hits = sample.str.match(_EMAIL_RE).sum()
    if email_hits / n > 0.9:
        return "email"

    gstin_hits = sample.str.match(_GSTIN_RE).sum()
    if gstin_hits / n > 0.9:
        return "gstin"

    pan_hits = sample.str.match(_PAN_RE).sum()
    if pan_hits / n > 0.9:
        return "pan"

    distinct = non_null.nunique()
    if distinct <= max(2, min(50, n // 10)):
        return "categorical"

    return "string"


def _format_patterns(non_null: pd.Series, limit: int = 5) -> list[dict[str, Any]]:
    """Mask the sample values and count how often each shape occurs."""
    sample = non_null.head(20_000)
    if sample.empty:
        return []

    def mask(value: str) -> str:
        out = []
        for char in str(value):
            if char.isdigit():
                out.append("9")
            elif char.isalpha():
                out.append("A")
            else:
                out.append(char)
        return "".join(out)

    patterns = sample.map(mask)
    counts = patterns.value_counts().head(limit)
    return [
        {"pattern": pattern, "count": int(count), "share": round(count / len(sample), 4)}
        for pattern, count in counts.items()
    ]


def _numeric_stats(non_null: pd.Series) -> tuple[float | None, float | None, int, str | None, str | None]:
    numeric = pd.to_numeric(non_null, errors="coerce").dropna()
    if numeric.empty:
        return None, None, 0, None, None

    mean = float(numeric.mean())
    stddev = float(numeric.std(ddof=0)) if len(numeric) > 1 else 0.0

    outlier_count = 0
    if len(numeric) >= 20 and stddev > 0:
        z = np.abs((numeric - mean) / stddev)
        # IQR is more robust than z-score for the skewed money columns, so use
        # both and take the union of what each flags.
        q1, q3 = numeric.quantile([0.25, 0.75])
        iqr = q3 - q1
        if iqr > 0:
            iqr_mask = (numeric < q1 - 3 * iqr) | (numeric > q3 + 3 * iqr)
        else:
            iqr_mask = pd.Series(False, index=numeric.index)
        outlier_count = int(((z > 4) | iqr_mask).sum())

    return mean, stddev, outlier_count, str(numeric.min()), str(numeric.max())


def _date_range(non_null: pd.Series) -> tuple[str | None, str | None, int]:
    """Parse with mixed formats and report the range plus unparseable count."""
    parsed = pd.to_datetime(non_null, errors="coerce", format="mixed", dayfirst=False)
    failures = int(parsed.isna().sum())
    valid = parsed.dropna()
    if valid.empty:
        return None, None, failures
    return valid.min().date().isoformat(), valid.max().date().isoformat(), failures


def profile_column(series: pd.Series, *, entity: str, column_name: str) -> ColumnProfile:
    row_count = int(len(series))
    null_mask = _is_null_series(series)
    null_count = int(null_mask.sum())
    non_null = series[~null_mask].str.strip()

    inferred = _infer_type(series, non_null)
    distinct_count = int(non_null.nunique())

    profile = ColumnProfile(
        entity=entity,
        column_name=column_name,
        row_count=row_count,
        null_count=null_count,
        null_pct=round(null_count / row_count * 100, 3) if row_count else 0.0,
        distinct_count=distinct_count,
        inferred_type=inferred,
        sample_values=non_null.head(5).tolist(),
    )

    if non_null.empty:
        profile.notes.append("Column is entirely empty.")
        return profile

    if inferred in ("int", "float"):
        mean, stddev, outliers, low, high = _numeric_stats(non_null)
        profile.mean_value = mean
        profile.stddev_value = stddev
        profile.outlier_count = outliers
        profile.min_value = low
        profile.max_value = high
        if outliers:
            profile.notes.append(f"{outliers} statistical outliers detected.")
    elif inferred in ("date", "date_mixed"):
        low, high, failures = _date_range(non_null)
        profile.min_value = low
        profile.max_value = high
        if inferred == "date_mixed":
            shares, _ = _date_format_breakdown(non_null)
            profile.format_patterns = [
                {"pattern": label, "count": int(round(share * len(non_null))), "share": share}
                for label, share in sorted(shares.items(), key=lambda kv: -kv[1])
            ]
            profile.notes.append(
                f"Mixed date formats across {len(shares)} styles; normalisation required."
            )
        if failures:
            profile.notes.append(f"{failures} values could not be parsed as a date.")
    elif inferred in ("email", "gstin", "pan"):
        pattern = {"email": _EMAIL_RE, "gstin": _GSTIN_RE, "pan": _PAN_RE}[inferred]
        bad = int((~non_null.str.match(pattern)).sum())
        if bad:
            profile.notes.append(f"{bad} values do not match the {inferred} format.")
    else:
        profile.format_patterns = _format_patterns(non_null)
        lengths = non_null.str.len()
        profile.min_value = f"len={int(lengths.min())}"
        profile.max_value = f"len={int(lengths.max())}"

    if inferred not in ("int", "float", "date", "date_mixed") and not profile.format_patterns:
        profile.format_patterns = _format_patterns(non_null)

    if profile.null_pct > 50:
        profile.notes.append(f"{profile.null_pct:.1f}% of values are empty.")

    return profile


def profile_entity(entity: str, frame: pd.DataFrame) -> list[ColumnProfile]:
    return [
        profile_column(frame[column], entity=entity, column_name=str(column))
        for column in frame.columns
    ]


def persist_profiles(session: Session, batch_id: str, profiles: list[ColumnProfile]) -> None:
    if not profiles:
        return
    payload = [
        {
            "batch_id": batch_id,
            "entity": p.entity,
            "column_name": p.column_name,
            "row_count": p.row_count,
            "null_count": p.null_count,
            "null_pct": p.null_pct,
            "distinct_count": p.distinct_count,
            "inferred_type": p.inferred_type,
            "format_patterns": _json(p.format_patterns),
            "min_value": p.min_value,
            "max_value": p.max_value,
            "mean_value": p.mean_value,
            "stddev_value": p.stddev_value,
            "outlier_count": p.outlier_count,
            "sample_values": _json(p.sample_values),
        }
        for p in profiles
    ]

    # One statement for the whole entity keeps a 13-entity batch to 13 round trips.
    session.execute(
        text(
            """
            insert into public.column_profiles (
                batch_id, entity, column_name, row_count, null_count, null_pct,
                distinct_count, inferred_type, format_patterns, min_value, max_value,
                mean_value, stddev_value, outlier_count, sample_values
            ) values (
                cast(:batch_id as uuid), :entity, :column_name, :row_count, :null_count,
                :null_pct, :distinct_count, :inferred_type, cast(:format_patterns as jsonb),
                :min_value, :max_value, :mean_value, :stddev_value, :outlier_count,
                cast(:sample_values as jsonb)
            )
            on conflict (batch_id, entity, column_name) do update
              set row_count = excluded.row_count,
                  null_count = excluded.null_count,
                  null_pct = excluded.null_pct,
                  distinct_count = excluded.distinct_count,
                  inferred_type = excluded.inferred_type,
                  format_patterns = excluded.format_patterns,
                  min_value = excluded.min_value,
                  max_value = excluded.max_value,
                  mean_value = excluded.mean_value,
                  stddev_value = excluded.stddev_value,
                  outlier_count = excluded.outlier_count,
                  sample_values = excluded.sample_values
            """
        ),
        payload,
    )


def _json(value: Any) -> str:
    import json

    return json.dumps(value, default=str)


def run_profile(
    session: Session,
    *,
    batch_id: str,
    frames: dict[str, pd.DataFrame],
    user=None,
    request_id: str | None = None,
) -> ProfileSummary:
    import time

    started = time.perf_counter()
    from app.pipeline.ingest import finish_stage, start_stage

    start_stage(session, batch_id, "profile")

    all_profiles: dict[str, list[ColumnProfile]] = {}
    total_rows = 0
    for entity, frame in frames.items():
        profiles = profile_entity(entity, frame)
        all_profiles[entity] = profiles
        total_rows += len(frame)
        persist_profiles(session, batch_id, profiles)

    duration_ms = int((time.perf_counter() - started) * 1000)
    total_columns = sum(len(p) for p in all_profiles.values())

    summary = {
        "entities": len(frames),
        "columns": total_columns,
        "rows": total_rows,
        "duration_ms": duration_ms,
        "empty_columns": sum(
            1 for profiles in all_profiles.values() for p in profiles if p.inferred_type == "empty"
        ),
        "high_null_columns": sum(
            1 for profiles in all_profiles.values() for p in profiles if p.null_pct > 50
        ),
    }

    finish_stage(session, batch_id, "profile", summary=summary)
    session.execute(
        text(
            """
            update public.migration_batches
               set status = 'profiled', record_count_in = :rows
             where id = cast(:b as uuid)
            """
        ),
        {"b": batch_id, "rows": total_rows},
    )

    audit.record(
        session,
        action="profile.completed",
        user=user,
        entity="column_profiles",
        batch_id=batch_id,
        stage="profile",
        after=summary,
        request_id=request_id,
    )

    return ProfileSummary(
        batch_id=batch_id,
        entities=all_profiles,
        total_columns=total_columns,
        total_rows=total_rows,
        duration_ms=duration_ms,
    )
