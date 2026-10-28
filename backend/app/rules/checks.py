"""Validation rule implementations.

Every check is vectorised: it works on whole columns with pandas and NumPy, then
materialises only the offending rows. Nothing loops over the full frame, so a
100,000-row entity stays inside the 60 second budget.

Each function has the signature::

    def check(frame, spec, params, context) -> list[dict]

and returns finding dictionaries. ``context`` carries the already-loaded frames
of other entities so referential rules can look up parent keys without a second
database round trip.

Findings are deliberately per-record. A rule that finds 300 bad GSTINs returns
300 findings, because the review queue works at record level and the detection
rate in the demo is counted per record.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Any, Callable

import numpy as np
import pandas as pd
from rapidfuzz import fuzz, process

from app.config import settings
from app.pipeline.normalise import DATE_FORMATS, is_title_case_ok, parse_dates
from app.pipeline.profile import NULL_TOKENS
from app.pipeline.entities import EntitySpec

logger = logging.getLogger(__name__)

# Kept small so findings stay reviewable; the count is always reported in full.
MAX_SAMPLES_PER_RULE = 5000


class RuleContext:
    """Everything a rule needs beyond its own frame."""

    def __init__(
        self,
        *,
        frames: dict[str, pd.DataFrame],
        specs: dict[str, EntitySpec],
        reference_date: date | None = None,
        max_findings: int = MAX_SAMPLES_PER_RULE,
    ) -> None:
        self.frames = frames
        self.specs = specs
        if reference_date is None:
            reference_date = date.fromisoformat(settings.reference_date)
        self.reference_date = reference_date
        self.max_findings = max_findings
        # Parent keys a child still points at, keyed by (parent entity, parent
        # column). The cleaner consults this before archiving a master record:
        # dropping a parent that an invoice still references does not clean the
        # data, it manufactures orphans.
        self.protected_keys: dict[tuple[str, str], set[str]] = {}

    def frame(self, entity: str) -> pd.DataFrame:
        return self.frames.get(entity, pd.DataFrame())

    def spec(self, entity: str) -> EntitySpec | None:
        return self.specs.get(entity)

    def parent_keys(self, entity: str, column: str) -> set[str]:
        frame = self.frame(entity)
        if frame.empty or column not in frame.columns:
            return set()
        series = frame[column].astype(str).str.strip()
        return set(series[~series.isin(NULL_TOKENS)])


def _blank_mask(series: pd.Series) -> pd.Series:
    stripped = series.astype(str).str.strip()
    return stripped.isin(NULL_TOKENS)


def _finding(
    *,
    rule_id: str,
    entity: str,
    record_id: str,
    field: str | None,
    message: str,
    severity: str,
    suggested_fix: str,
    auto_fixable: bool,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "rule_category": "",
        "severity": severity,
        "entity": entity,
        "record_id": record_id,
        "field": field,
        "message": message,
        "suggested_fix": suggested_fix,
        "auto_fixable": auto_fixable,
        "details": details or {},
    }


def _record_ids(frame: pd.DataFrame, mask: pd.Series) -> pd.Series:
    """Record ids for the masked rows.

    The mask may cover only part of the frame (a rule that works on a group, for
    example), so it is reindexed to the full frame before use.
    """
    aligned = mask.reindex(frame.index, fill_value=False).astype(bool)
    if "record_id" in frame.columns:
        return frame.loc[aligned, "record_id"].astype(str)
    return pd.Series(frame.index[aligned], index=frame.index[aligned]).astype(str)


def _align(mask: pd.Series, index: pd.Index) -> pd.Series:
    """Reindex a boolean mask to a full index, treating gaps as False."""
    return mask.reindex(index, fill_value=False).astype(bool)


def _numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.astype(str).str.strip(), errors="coerce")


# --------------------------------------------------------------- master data --


def check_duplicate_exact(
    frame: pd.DataFrame, spec: EntitySpec, params: dict, ctx: RuleContext
) -> list[dict]:
    """Whole-row duplicates on the business key.

    The survivor is the row with the smallest record id, not the first row in
    the file. File order is not stable — the demo generator shuffles rows — so
    choosing by position would flag a different row on each run and make the
    finding impossible to reproduce or reconcile.
    """
    key = params.get("key_column") or spec.primary_key
    if key not in frame.columns:
        return []

    values = frame[key].astype(str).str.strip()
    work = frame.loc[~values.isin(NULL_TOKENS)].copy()
    if work.empty:
        return []
    work["_key"] = values[~values.isin(NULL_TOKENS)]
    work["_sort"] = (
        work["record_id"].astype(str) if "record_id" in work.columns else work.index.astype(str)
    )

    counts = work["_key"].value_counts()
    duplicated = counts[counts > 1]
    if duplicated.empty:
        return []

    dup_rows = work[work["_key"].isin(duplicated.index)].sort_values("_sort")
    has_record_id = "record_id" in dup_rows.columns

    findings: list[dict] = []
    for value, group in dup_rows.groupby("_key", sort=False):
        survivor_id = str(group["_sort"].iloc[0])
        occurrence_count = int(duplicated[value])

        for _, row in group.iloc[1:].iterrows():
            record_id = str(row["record_id"]) if has_record_id else str(row.name)
            findings.append(
                _finding(
                    rule_id="",
                    entity=spec.name,
                    record_id=record_id,
                    field=key,
                    message=f"Duplicate {key} value {value!r} appears {occurrence_count} times.",
                    severity="blocker",
                    suggested_fix=(
                        f"Merge into {survivor_id} using the survivorship rule "
                        "(most complete, then most recent)."
                    ),
                    auto_fixable=True,
                    details={
                        "duplicate_value": value,
                        "occurrences": occurrence_count,
                        "survivor": survivor_id,
                    },
                )
            )
            if len(findings) >= ctx.max_findings:
                return findings
    return findings


def check_duplicate_fuzzy(
    frame: pd.DataFrame, spec: EntitySpec, params: dict, ctx: RuleContext
) -> list[dict]:
    """Near-duplicate names across different business keys.

    Uses RapidFuzz with a blocking pass: names are compared only within the same
    first-letter bucket, which turns an O(n^2) comparison into something that
    stays fast on thousands of master records.
    """
    match_columns = params.get("match_columns") or ["name"]
    threshold = float(params.get("score_threshold", 88))
    key = params.get("key_column") or spec.primary_key

    available = [c for c in match_columns if c in frame.columns]
    if not available or key not in frame.columns:
        return []

    working = frame.copy()
    working["_key"] = working[key].astype(str).str.strip()
    working = working[~_blank_mask(working["_key"])]
    working["_match"] = (
        working[available].astype(str).agg(" ".join, axis=1).str.lower().str.replace(r"\s+", " ", regex=True).str.strip()
    )
    working = working[working["_match"].str.len() > 0]

    if len(working) < 2:
        return []

    findings: list[dict] = []
    seen_pairs: set[tuple[str, str]] = set()

    working["_bucket"] = working["_match"].str[:2]
    for bucket, group in working.groupby("_bucket"):
        if len(group) < 2:
            continue
        choices = group["_match"].tolist()
        keys = group["_key"].tolist()
        record_ids = (
            group["record_id"].astype(str).tolist()
            if "record_id" in group.columns
            else [str(i) for i in group.index]
        )
        display = (
            group["name"].astype(str).tolist() if "name" in group.columns else choices
        )
        # cdist gives every pair score in one C call, far faster than a Python
        # double loop and still exact.
        scores = process.cdist(choices, choices, scorer=fuzz.token_sort_ratio, workers=-1)
        np.fill_diagonal(scores, 0)

        for i in range(len(choices)):
            for j in range(i + 1, len(choices)):
                score = float(scores[i][j])
                if score < threshold:
                    continue

                # Report against whichever record has the larger business key.
                # The key, not the file position, decides which record is the
                # canonical one, so the same pair produces the same finding no
                # matter how the source file was ordered.
                #
                # Pairs sharing a key are skipped: two rows with the same code
                # are an exact duplicate, which MD001 already reports, not a
                # fuzzy near-match. Without this the fuzzy rule would raise a
                # second finding on the clean survivor of every exact duplicate.
                if keys[i] == keys[j]:
                    continue
                if keys[i] < keys[j]:
                    keep, dupe = i, j
                else:
                    keep, dupe = j, i

                pair = tuple(sorted((record_ids[i], record_ids[j])))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)

                findings.append(
                    _finding(
                        rule_id="",
                        entity=spec.name,
                        record_id=record_ids[dupe],
                        field=available[0],
                        message=(
                            f"Name {display[dupe]!r} closely matches {display[keep]!r} "
                            f"({score:.0f}% similar) on a different code."
                        ),
                        severity="warn",
                        suggested_fix=(
                            f"Merge {record_ids[dupe]} into {record_ids[keep]} if they are the "
                            "same party, keeping the more complete and more recent profile."
                        ),
                        auto_fixable=True,
                        details={
                            "score": round(score, 2),
                            "match_record_id": record_ids[keep],
                            "match_value": choices[keep],
                            "key": keys[dupe],
                            "match_key": keys[keep],
                        },
                    )
                )
                if len(findings) >= ctx.max_findings:
                    return findings
    return findings


def check_missing_mandatory(
    frame: pd.DataFrame, spec: EntitySpec, params: dict, ctx: RuleContext
) -> list[dict]:
    columns = params.get("columns") or spec.mandatory_columns
    columns = [c for c in columns if c in frame.columns]
    if not columns:
        return []

    findings: list[dict] = []
    for column in columns:
        mask = _blank_mask(frame[column])
        if not mask.any():
            continue
        ids = _record_ids(frame, mask)
        for record_id in ids:
            findings.append(
                _finding(
                    rule_id="",
                    entity=spec.name,
                    record_id=record_id,
                    field=column,
                    message=f"Mandatory field {column} is empty.",
                    severity="blocker",
                    suggested_fix="Obtain the missing value from the source system owner before migrating.",
                    auto_fixable=False,
                    details={"column": column},
                )
            )
            if len(findings) >= ctx.max_findings:
                return findings
    return findings


def check_invalid_format(
    frame: pd.DataFrame, spec: EntitySpec, params: dict, ctx: RuleContext
) -> list[dict]:
    column = params.get("column")
    pattern = params.get("pattern")
    if not column or not pattern or column not in frame.columns:
        return []

    values = frame[column].astype(str).str.strip()
    present = ~values.isin(NULL_TOKENS)
    # Vectorised regex match over the whole column in one pass.
    matches = values.str.match(pattern, na=False)
    mask = present & ~matches
    if not mask.any():
        return []

    ids = _record_ids(frame, mask)
    offending = values[mask]
    findings = []
    for record_id, value in zip(ids, offending):
        findings.append(
            _finding(
                rule_id="",
                entity=spec.name,
                record_id=record_id,
                field=column,
                message=f"{column.upper()} {value!r} does not match the required format.",
                severity="warn",
                suggested_fix=f"Correct {column} against the source document, or blank it and flag for review.",
                auto_fixable=False,
                details={"column": column, "value": value, "expected_pattern": pattern},
            )
        )
        if len(findings) >= ctx.max_findings:
            break
    return findings


def check_referential_orphan(
    frame: pd.DataFrame, spec: EntitySpec, params: dict, ctx: RuleContext
) -> list[dict]:
    column = params.get("column")
    parent_entity = params.get("parent_entity")
    parent_column = params.get("parent_column")
    if not column or not parent_entity or not parent_column or column not in frame.columns:
        return []

    valid = ctx.parent_keys(parent_entity, parent_column)
    if not valid:
        return []

    values = frame[column].astype(str).str.strip()
    present = ~values.isin(NULL_TOKENS)
    mask = present & ~values.isin(valid)
    if not mask.any():
        return []

    ids = _record_ids(frame, mask)
    offending = values[mask]
    findings = []
    for record_id, value in zip(ids, offending):
        findings.append(
            _finding(
                rule_id="",
                entity=spec.name,
                record_id=record_id,
                field=column,
                message=f"{column} {value!r} has no matching record in {parent_entity}.",
                severity="blocker",
                suggested_fix=f"Create the missing {parent_entity[:-1]} or quarantine this record.",
                auto_fixable=False,
                details={
                    "column": column,
                    "value": value,
                    "parent_entity": parent_entity,
                    "parent_column": parent_column,
                },
            )
        )
        if len(findings) >= ctx.max_findings:
            break
    return findings


def check_uom_variant(
    frame: pd.DataFrame, spec: EntitySpec, params: dict, ctx: RuleContext
) -> list[dict]:
    column = params.get("column")
    variants = params.get("variants") or {}
    if not column or column not in frame.columns or not variants:
        return []

    # Map every alias, including the plain lower-case form of the canonical
    # code, then flag a row when its raw value differs from the canonical code.
    # That catches "Pcs" and "pcs" (inconsistent casing) while leaving a clean
    # "PCS" alone, without needing a special case for the canonical itself.
    lookup: dict[str, str] = {}
    for canonical, aliases in variants.items():
        for alias in aliases:
            lookup[alias.strip().lower()] = canonical

    values = frame[column].astype(str).str.strip()
    normalised = values.str.lower()
    mapped = normalised.map(lookup)
    # Only rows whose spelling is not already the canonical code are findings.
    mask = mapped.notna() & (values != mapped)
    if not mask.any():
        return []

    ids = _record_ids(frame, mask)
    findings = []
    for record_id, value, canonical in zip(ids, values[mask], mapped[mask]):
        findings.append(
            _finding(
                rule_id="",
                entity=spec.name,
                record_id=record_id,
                field=column,
                message=f"Unit {value!r} is a variant of the canonical unit {canonical!r}.",
                severity="warn",
                suggested_fix=f"Normalise {value!r} to {canonical}.",
                auto_fixable=True,
                details={"column": column, "value": value, "canonical": canonical},
            )
        )
        if len(findings) >= ctx.max_findings:
            break
    return findings


def check_stale_record(
    frame: pd.DataFrame, spec: EntitySpec, params: dict, ctx: RuleContext
) -> list[dict]:
    column = params.get("column", "last_activity_date")
    years = int(params.get("years", 3))
    if column not in frame.columns:
        return []

    parsed = parse_dates(frame[column])
    cutoff = pd.Timestamp(ctx.reference_date) - pd.DateOffset(years=years)
    mask = parsed.notna() & (parsed < cutoff)
    if not mask.any():
        return []

    ids = _record_ids(frame, mask)
    findings = []
    for record_id, when in zip(ids, parsed[mask]):
        findings.append(
            _finding(
                rule_id="",
                entity=spec.name,
                record_id=record_id,
                field=column,
                message=(
                    f"No activity since {when.date().isoformat()} "
                    f"({years} years). Archive rather than migrate."
                ),
                severity="warn",
                suggested_fix="Move to the archive set so the record is preserved but not loaded into the new ERP.",
                auto_fixable=True,
                details={"column": column, "last_activity": when.date().isoformat(), "years": years},
            )
        )
        if len(findings) >= ctx.max_findings:
            break
    return findings


_WHITESPACE_RE = re.compile(r"\s{2,}")


def _title_case_issue(values: pd.Series) -> pd.Series:
    """Rows whose casing is inconsistent, ignoring acronyms.

    Delegates to the shared normal form in ``pipeline.normalise`` so the check
    and the cleaner agree on what "correct casing" means. When the two had
    separate implementations the cleaner could rewrite a value the rule still
    flagged, and the finding would survive its own fix.
    """
    return ~is_title_case_ok(values)


def check_whitespace_case(
    frame: pd.DataFrame, spec: EntitySpec, params: dict, ctx: RuleContext
) -> list[dict]:
    columns = [c for c in (params.get("columns") or []) if c in frame.columns]
    title_columns = set(params.get("title_case_columns") or [])
    if not columns:
        return []

    findings = []
    for column in columns:
        values = frame[column].astype(str)
        stripped = values.str.strip()
        padded = values != stripped
        doubled = stripped.str.contains(_WHITESPACE_RE, regex=True, na=False)

        case_issue = pd.Series(False, index=frame.index)
        if column in title_columns:
            case_issue = _title_case_issue(values)

        mask = padded | doubled | case_issue
        if not mask.any():
            continue

        ids = _record_ids(frame, mask)
        issue_labels = pd.Series("", index=frame.index)
        issue_labels = issue_labels.mask(padded, "stray whitespace")
        issue_labels = issue_labels.mask(
            doubled, issue_labels.where(issue_labels == "", issue_labels + " and ") + "repeated spaces"
        )
        issue_labels = issue_labels.mask(
            case_issue, issue_labels.where(issue_labels == "", issue_labels + " and ") + "inconsistent casing"
        )

        for record_id, issue in zip(ids, issue_labels[mask]):
            findings.append(
                _finding(
                    rule_id="",
                    entity=spec.name,
                    record_id=record_id,
                    field=column,
                    message=f"Field {column} has {issue or 'formatting issues'}.",
                    severity="info",
                    suggested_fix="Trim whitespace, collapse repeated spaces and apply consistent title case.",
                    auto_fixable=True,
                    details={"column": column, "issues": issue},
                )
            )
            if len(findings) >= ctx.max_findings:
                return findings
    return findings


_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def check_date_format(
    frame: pd.DataFrame, spec: EntitySpec, params: dict, ctx: RuleContext
) -> list[dict]:
    columns = [c for c in (params.get("columns") or []) if c in frame.columns]
    if not columns:
        return []

    findings = []
    for column in columns:
        values = frame[column].astype(str).str.strip()
        non_blank = ~values.isin(NULL_TOKENS)
        non_iso = non_blank & ~values.str.match(_ISO_RE, na=False)
        # Only flag values that are actually dates in another format.
        parseable = parse_dates(values.where(non_iso)).notna()
        mask = non_iso & parseable
        if not mask.any():
            continue

        ids = _record_ids(frame, mask)
        for record_id, value in zip(ids, values[mask]):
            findings.append(
                _finding(
                    rule_id="",
                    entity=spec.name,
                    record_id=record_id,
                    field=column,
                    message=f"Date field {column} uses a non-ISO format ({value!r}).",
                    severity="info",
                    suggested_fix="Normalise to ISO 8601 (YYYY-MM-DD).",
                    auto_fixable=True,
                    details={"column": column, "value": value, "target_format": "%Y-%m-%d"},
                )
            )
            if len(findings) >= ctx.max_findings:
                return findings
    return findings




def check_mixed_date_formats(
    frame: pd.DataFrame, spec: EntitySpec, params: dict, ctx: RuleContext
) -> list[dict]:
    """Flag a column that is internally inconsistent about its date format.

    One finding per column, not per row: the problem is the column's shape, and
    the fix is to normalise the whole column at once.
    """
    columns = [c for c in (params.get("columns") or []) if c in frame.columns]
    min_formats = int(params.get("min_distinct_formats", 2))
    if not columns:
        return []

    findings = []
    for column in columns:
        values = frame[column].astype(str).str.strip()
        values = values[~values.isin(NULL_TOKENS)]
        if len(values) < 10:
            continue

        detected: dict[str, int] = {}
        for fmt in DATE_FORMATS:
            try:
                parsed = pd.to_datetime(values, format=fmt, errors="coerce")
            except (ValueError, TypeError):
                continue
            count = int(parsed.notna().sum())
            if count > 0:
                detected[fmt] = count

        if len(detected) < min_formats:
            continue

        findings.append(
            _finding(
                rule_id="",
                entity=spec.name,
                record_id=f"COLUMN:{column}",
                field=column,
                message=(
                    f"{column} contains {len(detected)} different date formats "
                    f"({', '.join(sorted(detected))})."
                ),
                severity="warn",
                suggested_fix="Normalise every value in the column to ISO 8601.",
                auto_fixable=True,
                details={"column": column, "formats": detected, "affected_rows": int(sum(detected.values()))},
            )
        )
    return findings


# ----------------------------------------------------------------- finance ---


def check_trial_balance(
    frame: pd.DataFrame, spec: EntitySpec, params: dict, ctx: RuleContext
) -> list[dict]:
    """Debits must equal credits per fiscal period."""
    debit_column = params.get("debit_column", "debit")
    credit_column = params.get("credit_column", "credit")
    group_columns = params.get("group_columns") or ["fiscal_year", "period"]
    tolerance = float(params.get("tolerance", 0.01))

    if debit_column not in frame.columns or credit_column not in frame.columns:
        return []

    group_columns = [c for c in group_columns if c in frame.columns]
    if not group_columns:
        return []

    working = frame.copy()
    working["_debit"] = _numeric(working[debit_column]).fillna(0.0)
    working["_credit"] = _numeric(working[credit_column]).fillna(0.0)

    grouped = working.groupby(group_columns, dropna=False)[["_debit", "_credit"]].sum()
    grouped["_difference"] = (grouped["_debit"] - grouped["_credit"]).round(2)
    unbalanced = grouped[grouped["_difference"].abs() > tolerance]
    if unbalanced.empty:
        return []

    findings = []
    for keys, row in unbalanced.iterrows():
        key_tuple = keys if isinstance(keys, tuple) else (keys,)
        label = "-".join(str(k) for k in key_tuple)

        # Attach the finding to the ledger lines in the offending period so the
        # review queue can show the actual rows.
        mask = pd.Series(True, index=working.index)
        for column, value in zip(group_columns, key_tuple):
            mask &= working[column].astype(str) == str(value)

        period_rows = working[mask]
        largest = period_rows.assign(_abs=period_rows["_debit"].abs()).nlargest(1, "_abs")
        record_id = (
            str(largest["record_id"].iloc[0])
            if "record_id" in largest.columns and len(largest)
            else label
        )

        findings.append(
            _finding(
                rule_id="",
                entity=spec.name,
                record_id=record_id,
                field=debit_column,
                message=(
                    f"Period {label} is out of balance by {row['_difference']:.2f} "
                    f"(debits {row['_debit']:.2f}, credits {row['_credit']:.2f})."
                ),
                severity="blocker",
                suggested_fix="Investigate the unbalanced period with the finance controller before loading.",
                auto_fixable=False,
                details={
                    "group": dict(zip(group_columns, [str(k) for k in key_tuple])),
                    "difference": float(row["_difference"]),
                    "debit_total": float(row["_debit"]),
                    "credit_total": float(row["_credit"]),
                    "affected_rows": int(mask.sum()),
                },
            )
        )
    return findings


def check_retired_reference(
    frame: pd.DataFrame, spec: EntitySpec, params: dict, ctx: RuleContext
) -> list[dict]:
    column = params.get("column")
    parent_entity = params.get("parent_entity")
    parent_column = params.get("parent_column")
    retired_column = params.get("retired_column", "is_retired")
    if not column or not parent_entity or column not in frame.columns:
        return []

    parent = ctx.frame(parent_entity)
    if parent.empty or parent_column not in parent.columns or retired_column not in parent.columns:
        return []

    retired_values = parent[parent[retired_column].astype(str).str.strip().str.lower().isin({"true", "t", "yes", "1"})]
    retired_codes = set(retired_values[parent_column].astype(str).str.strip())
    if not retired_codes:
        return []

    values = frame[column].astype(str).str.strip()
    mask = values.isin(retired_codes)
    if not mask.any():
        return []

    ids = _record_ids(frame, mask)
    findings = []
    for record_id, value in zip(ids, values[mask]):
        findings.append(
            _finding(
                rule_id="",
                entity=spec.name,
                record_id=record_id,
                field=column,
                message=f"Tax code {value!r} is retired and must not be used on migrated documents.",
                severity="blocker",
                suggested_fix="Map to the current equivalent tax code, or reclassify as exempt.",
                auto_fixable=True,
                details={"column": column, "value": value, "parent_entity": parent_entity},
            )
        )
        if len(findings) >= ctx.max_findings:
            break
    return findings


def check_currency_mismatch(
    frame: pd.DataFrame, spec: EntitySpec, params: dict, ctx: RuleContext
) -> list[dict]:
    column = params.get("column", "currency")
    parent_entity = params.get("parent_entity")
    parent_key = params.get("parent_key")
    parent_column = params.get("parent_column", "currency")
    if not column or not parent_entity or not parent_key or column not in frame.columns:
        return []

    parent = ctx.frame(parent_entity)
    if parent.empty or parent_key not in parent.columns or parent_column not in parent.columns:
        return []

    lookup = dict(
        zip(
            parent[parent_key].astype(str).str.strip(),
            parent[parent_column].astype(str).str.strip(),
        )
    )

    values = frame[column].astype(str).str.strip()
    keys = frame[parent_key].astype(str).str.strip()
    expected = keys.map(lookup)
    mask = expected.notna() & (values != expected) & (values != "")
    if not mask.any():
        return []

    ids = _record_ids(frame, mask)
    findings = []
    for record_id, value, parent_value in zip(ids, values[mask], expected[mask]):
        findings.append(
            _finding(
                rule_id="",
                entity=spec.name,
                record_id=record_id,
                field=column,
                message=f"Invoice currency {value!r} differs from the customer currency {parent_value!r}.",
                severity="warn",
                suggested_fix="Confirm the invoicing currency, then either convert or correct the customer record.",
                auto_fixable=False,
                details={"column": column, "value": value, "parent_value": parent_value},
            )
        )
        if len(findings) >= ctx.max_findings:
            break
    return findings


def check_negative_value(
    frame: pd.DataFrame, spec: EntitySpec, params: dict, ctx: RuleContext
) -> list[dict]:
    column = params.get("column")
    allow_zero = bool(params.get("allow_zero", True))
    if not column or column not in frame.columns:
        return []

    numeric = _numeric(frame[column])
    if allow_zero:
        mask = numeric < 0
    else:
        mask = numeric <= 0
    mask = mask.fillna(False)
    if not mask.any():
        return []

    ids = _record_ids(frame, mask)
    findings = []
    for record_id, value in zip(ids, numeric[mask]):
        findings.append(
            _finding(
                rule_id="",
                entity=spec.name,
                record_id=record_id,
                field=column,
                message=f"{column} is negative ({value:g}).",
                severity="blocker",
                suggested_fix="Recount the physical stock and correct the opening balance.",
                auto_fixable=False,
                details={"column": column, "value": float(value)},
            )
        )
        if len(findings) >= ctx.max_findings:
            break
    return findings


def check_impossible_date(
    frame: pd.DataFrame, spec: EntitySpec, params: dict, ctx: RuleContext
) -> list[dict]:
    columns = [c for c in (params.get("date_columns") or []) if c in frame.columns]
    order_pairs = params.get("order_pairs") or []
    earliest = pd.Timestamp(params.get("earliest", "1990-01-01"))
    allow_future_days = int(params.get("allow_future_days", 0))
    if not columns:
        return []

    latest_allowed = pd.Timestamp(ctx.reference_date) + pd.Timedelta(days=allow_future_days)
    findings: list[dict] = []

    for column in columns:
        parsed = pd.to_datetime(frame[column].astype(str).str.strip(), errors="coerce", format="mixed")
        future = parsed.notna() & (parsed > latest_allowed)
        ancient = parsed.notna() & (parsed < earliest)

        for mask, reason in ((future, "Date is in the future"), (ancient, "Date predates the company")):
            if not mask.any():
                continue
            ids = _record_ids(frame, mask)
            for record_id, value in zip(ids, parsed[mask]):
                findings.append(
                    _finding(
                        rule_id="",
                        entity=spec.name,
                        record_id=record_id,
                        field=column,
                        message=f"{reason} ({column} = {value.date().isoformat()}).",
                        severity="blocker",
                        suggested_fix="Correct the date against the source document.",
                        auto_fixable=False,
                        details={"column": column, "value": value.date().isoformat(), "reason": reason},
                    )
                )
                if len(findings) >= ctx.max_findings:
                    return findings

    for before_column, after_column in order_pairs:
        if before_column not in frame.columns or after_column not in frame.columns:
            continue
        before = pd.to_datetime(frame[before_column].astype(str).str.strip(), errors="coerce", format="mixed")
        after = pd.to_datetime(frame[after_column].astype(str).str.strip(), errors="coerce", format="mixed")
        mask = before.notna() & after.notna() & (after < before)
        if not mask.any():
            continue
        ids = _record_ids(frame, mask)
        for record_id, value in zip(ids, after[mask]):
            findings.append(
                _finding(
                    rule_id="",
                    entity=spec.name,
                    record_id=record_id,
                    field=after_column,
                    message=(
                        f"{after_column} ({value.date().isoformat()}) falls before "
                        f"{before_column}."
                    ),
                    severity="blocker",
                    suggested_fix=f"Correct {after_column} so it is on or after {before_column}.",
                    auto_fixable=False,
                    details={
                        "column": after_column,
                        "value": value.date().isoformat(),
                        "reason": f"{after_column} precedes {before_column}",
                    },
                )
            )
            if len(findings) >= ctx.max_findings:
                return findings

    return findings


def check_statistical_outlier(
    frame: pd.DataFrame, spec: EntitySpec, params: dict, ctx: RuleContext
) -> list[dict]:
    """Flag values far from their peer group.

    Two rules share this implementation: a salary rule that compares against the
    department median, and a generic numeric rule that uses z-score and IQR.
    """
    columns = params.get("column")
    if isinstance(columns, str):
        columns = [columns]
    columns = [c for c in (columns or []) if c in frame.columns]
    if not columns:
        return []

    group_by = [c for c in (params.get("group_by") or []) if c in frame.columns]
    multiplier = params.get("multiplier")
    z_threshold = float(params.get("zscore_threshold", 4.0))
    iqr_multiplier = float(params.get("iqr_multiplier", 3.0))

    findings = []
    for column in columns:
        numeric = _numeric(frame[column])
        valid = numeric.dropna()
        if len(valid) < 20:
            continue

        if multiplier is not None and group_by:
            groups = frame.groupby(group_by, dropna=False).groups
            for group_keys, indices in groups.items():
                subset = numeric.loc[numeric.index.intersection(indices)].dropna()
                if len(subset) < 10:
                    continue
                median = float(subset.median())
                if median <= 0:
                    continue
                # Expand back to the full index so every later step aligns.
                ratio = (numeric / median).where(numeric.index.isin(subset.index))
                mask = _align(ratio > float(multiplier), frame.index)
                if not mask.any():
                    continue
                ids = _record_ids(frame, mask)
                for record_id, value in zip(ids, numeric[mask]):
                    label = group_keys if isinstance(group_keys, str) else "-".join(str(k) for k in group_keys)
                    findings.append(
                        _finding(
                            rule_id="",
                            entity=spec.name,
                            record_id=record_id,
                            field=column,
                            message=(
                                f"{column} value {value:,.2f} is "
                                f"{float(value) / median:.1f}x the {label} median ({median:,.2f})."
                            ),
                            severity="warn",
                            suggested_fix="Verify the value with the owning department; it may be a data entry error.",
                            auto_fixable=False,
                            details={
                                "column": column,
                                "value": float(value),
                                "median": median,
                                "group": label,
                                "ratio": round(float(value) / median, 3),
                            },
                        )
                    )
                    if len(findings) >= ctx.max_findings:
                        return findings
        else:
            mean = float(valid.mean())
            stddev = float(valid.std(ddof=0))
            q1, q3 = valid.quantile([0.25, 0.75])
            iqr = float(q3 - q1)

            mask = pd.Series(False, index=frame.index)
            if stddev > 0:
                z = (numeric - mean) / stddev
                mask |= _align(z.abs() > z_threshold, frame.index)
            if iqr > 0:
                mask |= _align(
                    (numeric < q1 - iqr_multiplier * iqr) | (numeric > q3 + iqr_multiplier * iqr),
                    frame.index,
                )
            if not mask.any():
                continue

            z_scores = ((numeric - mean) / stddev) if stddev > 0 else pd.Series(0.0, index=numeric.index)
            ids = _record_ids(frame, mask)
            for record_id, value, zscore in zip(ids, numeric[mask], z_scores[mask]):
                findings.append(
                    _finding(
                        rule_id="",
                        entity=spec.name,
                        record_id=record_id,
                        field=column,
                        message=f"{column} value {value:,.2f} is a statistical outlier (z-score {zscore:.1f}).",
                        severity="info",
                        suggested_fix="Review the value; it may be a legitimate extreme or a data entry error.",
                        auto_fixable=False,
                        details={"column": column, "value": float(value), "zscore": round(float(zscore), 2)},
                    )
                )
                if len(findings) >= ctx.max_findings:
                    return findings
    return findings


def check_arithmetic_check(
    frame: pd.DataFrame, spec: EntitySpec, params: dict, ctx: RuleContext
) -> list[dict]:
    result_column = params.get("result_column")
    operands = params.get("operands") or []
    operator = params.get("operator", "add")
    tolerance = float(params.get("tolerance", 0.01))
    if not result_column or not operands:
        return []
    if result_column not in frame.columns or any(o not in frame.columns for o in operands):
        return []

    result = _numeric(frame[result_column])
    parts = [_numeric(frame[o]) for o in operands]
    if operator == "add":
        expected = sum(parts)
    elif operator == "subtract":
        expected = parts[0]
        for part in parts[1:]:
            expected = expected - part
    else:
        return []

    mask = result.notna() & expected.notna() & ((result - expected).abs() > tolerance)
    if not mask.any():
        return []

    ids = _record_ids(frame, mask)
    findings = []
    for record_id, value, exp in zip(ids, result[mask], expected[mask]):
        findings.append(
            _finding(
                rule_id="",
                entity=spec.name,
                record_id=record_id,
                field=result_column,
                message=f"{result_column} {value:,.2f} does not equal {' plus '.join(operands)} ({exp:,.2f}).",
                severity="warn",
                suggested_fix="Recalculate the total from its components.",
                auto_fixable=False,
                details={
                    "column": result_column,
                    "value": float(value),
                    "expected": float(exp),
                    "difference": round(float(value - exp), 2),
                },
            )
        )
        if len(findings) >= ctx.max_findings:
            break
    return findings


# ----------------------------------------------------------------- registry ---

RULES: dict[str, Callable[[pd.DataFrame, EntitySpec, dict, RuleContext], list[dict]]] = {
    "duplicate_exact": check_duplicate_exact,
    "duplicate_fuzzy": check_duplicate_fuzzy,
    "missing_mandatory": check_missing_mandatory,
    "invalid_format": check_invalid_format,
    "referential_orphan": check_referential_orphan,
    "uom_variant": check_uom_variant,
    "stale_record": check_stale_record,
    "whitespace_case": check_whitespace_case,
    "date_format": check_date_format,
    "mixed_date_formats": check_mixed_date_formats,
    "trial_balance": check_trial_balance,
    "retired_reference": check_retired_reference,
    "currency_mismatch": check_currency_mismatch,
    "negative_value": check_negative_value,
    "impossible_date": check_impossible_date,
    "statistical_outlier": check_statistical_outlier,
    "arithmetic_check": check_arithmetic_check,
}


def registered_types() -> set[str]:
    return set(RULES)
