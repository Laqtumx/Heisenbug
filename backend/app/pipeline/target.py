"""Mapping a cleaned legacy frame onto the target ERP tables.

Dry run, load and rollback all need the same answer to the same questions: which
legacy column becomes which target column, which values must be encrypted on the
way in, and what does a row of this entity look like once it is in the target
shape. That logic lives here once so the three stages cannot disagree about it —
a disagreement would mean the dry run reconciles a load that the real load does
not perform.

The mapping comes from ``entities.py``, which is the single declaration of the
legacy-to-target contract. Nothing here hardcodes a column name.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.pipeline.entities import EntitySpec
from app.pipeline.profile import NULL_TOKENS
from app.security.crypto import blind_index, encrypt

logger = logging.getLogger(__name__)

# Numeric columns whose totals must reconcile between the cleaned frames and
# what actually landed. These are the figures a finance controller checks, so a
# silent rounding or truncation shows up here rather than at month end.
MONEY_COLUMNS: dict[str, tuple[str, ...]] = {
    "gl_balances": ("debit", "credit"),
    "invoices": ("subtotal", "tax_amount", "total_amount"),
    "purchase_orders": ("total_amount",),
    "items": ("standard_cost", "list_price", "reorder_level"),
    "customers": ("credit_limit",),
    "supplier_catalog": ("unit_price", "min_order_qty"),
}

# Non-numeric columns that still have to add up.
COUNT_COLUMNS: dict[str, tuple[str, ...]] = {
    "inventory_stock": ("qty_on_hand",),
}


class TargetLoadError(RuntimeError):
    """Raised when a frame cannot be expressed in the target shape."""


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    text_value = str(value).strip()
    return text_value == "" or text_value in NULL_TOKENS


def _cast(value: Any, dtype: str) -> Any:
    """Convert a legacy string to the Python type the target column expects.

    Legacy data is text even when the value is a number, so the cast is not
    optional. A blank becomes None rather than 0: an unknown quantity and a
    quantity of zero are different facts, and the schema distinguishes them.
    """
    if _is_blank(value):
        return None
    if dtype == "int":
        try:
            return int(float(str(value).strip()))
        except (TypeError, ValueError):
            return None
    if dtype == "float":
        try:
            return float(str(value).strip())
        except (TypeError, ValueError):
            return None
    if dtype == "bool":
        return str(value).strip().lower() in {"true", "t", "yes", "1", "y"}
    if dtype == "date":
        parsed = pd.to_datetime(str(value).strip(), errors="coerce", format="mixed")
        return None if pd.isna(parsed) else parsed.date()
    return str(value).strip()


def frame_to_rows(
    spec: EntitySpec,
    frame: pd.DataFrame,
    batch_id: str,
    available: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Convert a cleaned frame into target-shaped rows.

    Sensitive columns are encrypted here, so a plaintext tax id never reaches
    the target table. A blind index is written alongside when the table has a
    column for it: the index is what duplicate detection and lookups use, and
    the encrypted value is what an authorised reader decrypts. The payroll table
    has no index columns, so ``available`` lets the caller say what the table
    actually holds rather than guessing from the spec.
    """
    rows: list[dict[str, Any]] = []
    columns = spec.columns
    frame = frame.fillna("")

    for record in frame.to_dict(orient="records"):
        row: dict[str, Any] = {"batch_id": batch_id}
        for column in columns:
            target = column.target
            if not target:
                continue
            raw = record.get(column.name)
            value = _cast(raw, column.dtype)

            if column.sensitive:
                encrypted_column = f"{target}_encrypted"
                hash_column = f"{target}_hash"
                # A blank sensitive value stays blank: there is nothing to
                # protect and nothing to look up.
                if available is not None and encrypted_column not in available:
                    continue
                if value is None:
                    row[encrypted_column] = None
                    if available is None or hash_column in available:
                        row[hash_column] = None
                else:
                    row[encrypted_column] = encrypt(str(value))
                    if available is None or hash_column in available:
                        row[hash_column] = blind_index(str(value))
            else:
                if available is not None and target not in available:
                    continue
                row[target] = value
        rows.append(row)
    return rows


def table_columns(session: Session, schema: str, table: str) -> set[str]:
    """The column names a table actually has.

    The target schema is the authority on its own shape. Reading it here means a
    column added to the spec but not yet migrated, or a derived column a
    particular table does not carry, cannot cause an insert to fail.
    """
    rows = session.execute(
        text(
            """
            select column_name
              from information_schema.columns
             where table_schema = :schema and table_name = :table
            """
        ),
        {"schema": schema, "table": table},
    ).scalars().all()
    return {str(r) for r in rows}


def row_checksum(rows: list[dict[str, Any]], spec: EntitySpec) -> str:
    """A stable digest of the rows that matter, order-independent.

    Only identity and money columns are included. Encrypted values are excluded
    because Fernet is non-deterministic: the same plaintext encrypts to a
    different token each time, so including it would make two runs of an
    identical load look different. The blind index is deterministic and stands in
    for the sensitive columns instead.
    """
    keys = {spec.primary_key}
    keys.update(MONEY_COLUMNS.get(spec.name, ()))
    keys.update(COUNT_COLUMNS.get(spec.name, ()))
    for column in spec.columns:
        if column.sensitive and column.target:
            keys.add(f"{column.target}_hash")
    keys.discard(spec.primary_key)
    keys.add(spec.primary_key)

    digest = hashlib.sha256()
    normalised = []
    for row in rows:
        normalised.append(
            tuple((key, str(row.get(key))) for key in sorted(keys) if key in row)
        )
    for item in sorted(normalised, key=lambda t: tuple(str(pair) for pair in t)):
        digest.update(json.dumps(item, default=str).encode())
    return digest.hexdigest()


def frame_totals(spec: EntitySpec, frame: pd.DataFrame) -> dict[str, float]:
    """Sums of the money and quantity columns for one entity's frame."""
    totals: dict[str, float] = {}
    for column in MONEY_COLUMNS.get(spec.name, ()) + COUNT_COLUMNS.get(spec.name, ()):
        if column not in frame.columns:
            continue
        values = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
        totals[column] = round(float(values.sum()), 2)
    return totals


def row_totals(spec: EntitySpec, rows: list[dict[str, Any]]) -> dict[str, float]:
    """The same sums, taken from target-shaped rows."""
    totals: dict[str, float] = {}
    for column in MONEY_COLUMNS.get(spec.name, ()) + COUNT_COLUMNS.get(spec.name, ()):
        total = 0.0
        for row in rows:
            value = row.get(column)
            if value is not None:
                total += float(value)
        totals[column] = round(total, 2)
    return totals


def delete_batch_rows(session: Session, schema: str, spec: EntitySpec, batch_id: str) -> int:
    """Remove this batch's rows from one target table. Returns the count.

    This is what makes a load idempotent: loading a batch twice replaces its rows
    rather than duplicating them, and rollback is the same call.
    """
    result = session.execute(
        text(
            f"delete from {schema}.{spec.target_table} where batch_id = cast(:b as uuid)"
        ),
        {"b": batch_id},
    )
    return int(result.rowcount or 0)


def insert_rows(
    session: Session, schema: str, spec: EntitySpec, rows: list[dict[str, Any]]
) -> int:
    if not rows:
        return 0

    # Every row for an entity shares its columns, so the column list is taken
    # from the first row. A row missing a key would otherwise silently drop the
    # column for the whole insert.
    columns = sorted(rows[0].keys())
    for row in rows:
        if set(row.keys()) != set(columns):
            raise TargetLoadError(
                f"{spec.name}: rows have differing columns, refusing to load"
            )

    placeholders = ", ".join(f":{c}" for c in columns)
    statement = text(
        f"insert into {schema}.{spec.target_table} ({', '.join(columns)}) "
        f"values ({placeholders})"
    )
    session.execute(statement, rows)
    return len(rows)


def fetch_rows(session: Session, schema: str, spec: EntitySpec, batch_id: str) -> list[dict]:
    """Read a batch's rows back from a target table, in the target shape."""
    rows = session.execute(
        text(
            f"select * from {schema}.{spec.target_table} "
            "where batch_id = cast(:b as uuid)"
        ),
        {"b": batch_id},
    ).mappings().all()
    return [dict(row) for row in rows]


def verify_row_counts(
    session: Session, schema: str, spec: EntitySpec, batch_id: str, expected: int
) -> int:
    actual = session.execute(
        text(
            f"select count(*) from {schema}.{spec.target_table} "
            "where batch_id = cast(:b as uuid)"
        ),
        {"b": batch_id},
    ).scalar_one()
    return int(actual)
