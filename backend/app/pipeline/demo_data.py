"""Synthetic legacy ERP dataset with labelled defects.

The generator writes two things:

1. One CSV per entity, in the messy shape a real legacy export arrives in:
   mixed date formats, inconsistent casing, padded strings, variant unit codes.
2. A defect manifest CSV listing every defect that was deliberately injected,
   with the entity, record, field and the rule that is expected to catch it.

The manifest is what makes the detection claim measurable. Without it, "we catch
95% of problems" is an assertion. With it, it is a number a test can check.

Generation is seeded, so the same seed always produces the same dataset and the
same manifest, which keeps the test suite and the demo stable.
"""

from __future__ import annotations

import csv
import logging
import random
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from app.pipeline.entities import ENTITIES, ENTITIES_BY_NAME, EntitySpec

logger = logging.getLogger(__name__)

# Deterministic base so every clone produces an identical dataset.
DEFAULT_SEED = 20260101


@dataclass
class Defect:
    """One deliberately injected defect. This is the unit of ground truth."""

    defect_id: str
    defect_type: str
    entity: str
    record_id: str
    field: str
    expected_rule: str
    severity: str
    auto_fixable: bool
    description: str


@dataclass
class DefectPlan:
    """How many of each defect to inject."""

    duplicate_exact: int = 120
    duplicate_fuzzy: int = 150
    missing_mandatory: int = 200
    invalid_gstin: int = 150
    invalid_pan: int = 120
    invalid_email: int = 100
    invalid_pincode: int = 100
    invalid_phone: int = 80
    uom_variant: int = 300
    uom_unknown: int = 60
    stale_record: int = 250
    whitespace_case: int = 300
    date_format: int = 400
    orphan_customer: int = 200
    orphan_supplier: int = 150
    orphan_category: int = 120
    orphan_account: int = 100
    unbalanced_ledger: int = 60
    retired_tax_code: int = 150
    currency_mismatch: int = 120
    negative_stock: int = 150
    impossible_date: int = 150
    duplicate_invoice: int = 80
    salary_outlier: int = 40
    negative_amount: int = 60


@dataclass
class RecordCounts:
    suppliers: int = 1500
    customers: int = 3000
    items: int = 6000
    categories: int = 120
    uom: int = 25
    tax_codes: int = 40
    chart_of_accounts: int = 300
    gl_balances: int = 6000
    purchase_orders: int = 6000
    invoices: int = 15000
    inventory_stock: int = 4000
    supplier_catalog: int = 4000
    employees_payroll_summary: int = 500

    def total(self) -> int:
        return sum(asdict(self).values())


@dataclass
class DemoDataset:
    frames: dict[str, pd.DataFrame]
    defects: list[Defect]
    seed: int
    generated_at: str = ""

    @property
    def total_records(self) -> int:
        return int(sum(len(f) for f in self.frames.values()))

    def manifest_frame(self) -> pd.DataFrame:
        if not self.defects:
            return pd.DataFrame(
                columns=[
                    "defect_id", "defect_type", "entity", "record_id", "field",
                    "expected_rule", "severity", "auto_fixable", "description",
                ]
            )
        return pd.DataFrame([asdict(d) for d in self.defects])

    def write(self, out_dir: Path) -> dict[str, Path]:
        """Write one CSV per entity plus the manifest. Returns the paths written."""
        out_dir.mkdir(parents=True, exist_ok=True)
        written: dict[str, Path] = {}
        for name, frame in self.frames.items():
            path = out_dir / f"{name}.csv"
            frame.to_csv(path, index=False, quoting=csv.QUOTE_MINIMAL)
            written[name] = path
            logger.info("wrote %s (%s rows)", path.name, len(frame))
        manifest_path = out_dir / "defects_manifest.csv"
        self.manifest_frame().to_csv(manifest_path, index=False)
        written["defects_manifest"] = manifest_path
        return written


# --------------------------------------------------------------- vocabulary --

_GSTIN_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_NAME_PREFIX = [
    "Acme", "Northwind", "Contoso", "Globex", "Initech", "Umbrella", "Soylent",
    "Vandelay", "Stark", "Wayne", "Wonka", "Cyberdyne", "Massive", "Aperture",
    "Tyrell", "Oscorp", "Pied Piper", "Hooli", "Dunder", "Prestige",
]
_NAME_SUFFIX = [
    "Industries", "Traders", "Enterprises", "Solutions", "Logistics", "Textiles",
    "Engineering", "Polymers", "Metals", "Electricals", "Packaging", "Chemicals",
]
_LEGAL_FORM = ["Pvt Ltd", "Ltd", "LLP", "Inc", "and Sons", "Industries"]
_CITIES = [
    ("Mumbai", "Maharashtra", "400001"),
    ("Pune", "Maharashtra", "411001"),
    ("Bengaluru", "Karnataka", "560001"),
    ("Chennai", "Tamil Nadu", "600001"),
    ("Hyderabad", "Telangana", "500001"),
    ("Ahmedabad", "Gujarat", "380001"),
    ("Delhi", "Delhi", "110001"),
    ("Kolkata", "West Bengal", "700001"),
    ("Jaipur", "Rajasthan", "302001"),
    ("Kochi", "Kerala", "682001"),
]
_DEPARTMENTS = ["Finance", "Operations", "Sales", "Procurement", "IT", "HR", "Quality"]
_PAYMENT_TERMS = ["NET30", "NET45", "NET60", "NET15", "COD", "Advance"]
_CURRENCIES = ["INR", "INR", "INR", "INR", "USD", "EUR"]
_ITEM_WORDS = [
    "Bearing", "Gasket", "Valve", "Sensor", "Cable", "Motor", "Pump", "Filter",
    "Bolt", "Bracket", "Coupling", "Relay", "Fuse", "Panel", "Gear", "Seal",
    "Hose", "Clamp", "Bushing", "Actuator",
]
_ITEM_QUALIFIERS = ["Heavy Duty", "Standard", "Industrial", "Precision", "Compact", "Marine"]

# Canonical units, then the messy variants a legacy export actually contains.
CANONICAL_UOM = ["KG", "PCS", "LTR", "BOX", "MTR", "SET", "PKT", "TON"]
UOM_VARIANTS: dict[str, list[str]] = {
    "KG": ["Kgs", "kgs", "Kg.", " KG", "K.G."],
    "PCS": ["Pcs", "pcs", "Pcs.", " PC", "Nos", "nos"],
    "LTR": ["Ltr", "ltr", "Litre", " Lit", "LTRS"],
    "BOX": ["Box", "box", "Bx", " BOX", "Boxes"],
    "MTR": ["Mtr", "mtr", "Meter", " MTR", "Meters"],
    "SET": ["Set", "set", "Set.", " SET", "Sets"],
    "PKT": ["Pkt", "pkt", "Packet", " PKT", "Packets"],
    "TON": ["Ton", "ton", "Tonne", " TON", "Tonnes"],
}

_ACCOUNT_TYPES = ["Asset", "Liability", "Equity", "Revenue", "Expense"]

# Date formats deliberately mixed so normalisation has something to fix.
_DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%d-%b-%Y", "%Y%m%d", "%d.%m.%Y"]


def _record_id(prefix: str, index: int) -> str:
    return f"{prefix}-{index:06d}"


def _gstin(rng: random.Random) -> str:
    """A well-formed GSTIN: 2 state digits, 10-char PAN, entity, Z, checksum."""
    state = rng.choice([f"{n:02d}" for n in range(1, 38)])
    pan = "".join(rng.choice(_GSTIN_ALPHABET) for _ in range(5))
    pan += "".join(str(rng.randint(0, 9)) for _ in range(4))
    pan += rng.choice(_GSTIN_ALPHABET)
    entity = str(rng.randint(1, 9))
    checksum = rng.choice(_GSTIN_ALPHABET)
    return f"{state}{pan}{entity}Z{checksum}"


def _pan(rng: random.Random) -> str:
    return (
        "".join(rng.choice(_GSTIN_ALPHABET) for _ in range(5))
        + "".join(str(rng.randint(0, 9)) for _ in range(4))
        + rng.choice(_GSTIN_ALPHABET)
    )


def _phone(rng: random.Random) -> str:
    return f"+91{rng.randint(6000000000, 9999999999)}"


def _company_name(rng: random.Random) -> str:
    return (
        f"{rng.choice(_NAME_PREFIX)} {rng.choice(_NAME_SUFFIX)} "
        f"{rng.choice(_LEGAL_FORM)}"
    )


def _fuzzy_variant(rng: random.Random, name: str) -> str:
    """Produce a near-duplicate name: a plausible human data-entry error."""
    kind = rng.choice(["swap", "drop", "case", "space", "punct", "abbrev"])
    if kind == "swap" and len(name) > 6:
        i = rng.randrange(2, len(name) - 3)
        return name[:i] + name[i + 1] + name[i] + name[i + 2 :]
    if kind == "drop" and len(name) > 8:
        i = rng.randrange(3, len(name) - 3)
        return name[:i] + name[i + 1 :]
    if kind == "case":
        return name.upper()
    if kind == "space":
        return name.replace(" ", "  ", 1)
    if kind == "punct":
        return name.replace("Pvt Ltd", "Pvt. Ltd.").replace("Ltd", "Ltd.")
    return name.replace("Industries", "Inds").replace("Enterprises", "Ents")


def _mixed_date(rng: random.Random, value: date) -> str:
    """Render a date in one of several legacy formats."""
    return value.strftime(rng.choice(_DATE_FORMATS))


# ------------------------------------------------------------------ builder --


class DemoDataGenerator:
    """Builds the dataset and records every defect it injects."""

    def __init__(
        self,
        *,
        seed: int = DEFAULT_SEED,
        counts: RecordCounts | None = None,
        plan: DefectPlan | None = None,
        reference_date: date | None = None,
    ) -> None:
        self.seed = seed
        self.rng = random.Random(seed)
        self.np_rng = np.random.default_rng(seed)
        self.counts = counts or RecordCounts()
        self.plan = plan or DefectPlan()
        self.reference_date = reference_date or date(2026, 1, 1)
        self.defects: list[Defect] = []
        self._defect_seq = 0
        self.frames: dict[str, pd.DataFrame] = {}

    # ------------------------------------------------------------- helpers --
    def _add_defect(
        self,
        defect_type: str,
        entity: str,
        record_id: str,
        field: str,
        expected_rule: str,
        severity: str,
        auto_fixable: bool,
        description: str,
    ) -> None:
        self._defect_seq += 1
        self.defects.append(
            Defect(
                defect_id=f"DEF-{self._defect_seq:06d}",
                defect_type=defect_type,
                entity=entity,
                record_id=record_id,
                field=field,
                expected_rule=expected_rule,
                severity=severity,
                auto_fixable=auto_fixable,
                description=description,
            )
        )

    def _sample_indices(self, n_rows: int, n_defects: int) -> np.ndarray:
        """Distinct row positions to damage, so counts stay exact."""
        n = min(n_defects, n_rows)
        return self.np_rng.choice(n_rows, size=n, replace=False)

    def _pick(self, seq: list | np.ndarray, count: int) -> list:
        count = min(count, len(seq))
        return self.rng.sample(list(seq), count)

    # -------------------------------------------------------------- static --
    def build_categories(self) -> pd.DataFrame:
        n = self.counts.categories
        rows = []
        roots = ["RAW", "FIN", "CON", "SPR", "PKG", "ELE"]
        for i in range(n):
            code = f"CAT-{i + 1:04d}"
            if i < len(roots):
                rows.append(
                    {
                        "record_id": _record_id("CAT", i + 1),
                        "category_code": code,
                        "name": f"{roots[i].title()} Materials",
                        "parent_code": "",
                        "is_active": "true",
                    }
                )
            else:
                parent = f"CAT-{(i % len(roots)) + 1:04d}"
                rows.append(
                    {
                        "record_id": _record_id("CAT", i + 1),
                        "category_code": code,
                        "name": f"{self.rng.choice(_ITEM_WORDS)} {i}",
                        "parent_code": parent,
                        "is_active": "true",
                    }
                )
        return pd.DataFrame(rows)

    def build_uom(self) -> pd.DataFrame:
        rows = [
            {
                "record_id": _record_id("UOM", i + 1),
                "uom_code": code,
                "name": {
                    "KG": "Kilogram", "PCS": "Pieces", "LTR": "Litre", "BOX": "Box",
                    "MTR": "Metre", "SET": "Set", "PKT": "Packet", "TON": "Tonne",
                }.get(code, code),
                "base_uom_code": "" if code in ("KG", "PCS") else code,
                "conversion_factor": 1.0 if code in ("KG", "PCS") else round(
                    self.rng.uniform(0.1, 100), 4
                ),
            }
            for i, code in enumerate(CANONICAL_UOM)
        ]
        # Extra units so the master has more than the canonical set.
        for i in range(len(CANONICAL_UOM), self.counts.uom):
            code = f"UOM{i:02d}"
            rows.append(
                {
                    "record_id": _record_id("UOM", i + 1),
                    "uom_code": code,
                    "name": f"Auxiliary unit {i}",
                    "base_uom_code": "PCS",
                    "conversion_factor": round(self.rng.uniform(1, 50), 4),
                }
            )
        return pd.DataFrame(rows)

    def build_tax_codes(self) -> pd.DataFrame:
        rows = []
        standard = [
            ("GST5", 5.0, "GST"), ("GST12", 12.0, "GST"), ("GST18", 18.0, "GST"),
            ("GST28", 28.0, "GST"), ("EXEMPT", 0.0, "GST"), ("ZERO", 0.0, "GST"),
        ]
        for i in range(self.counts.tax_codes):
            if i < len(standard):
                code, rate, tax_type = standard[i]
            else:
                code, rate, tax_type = f"TC{i:03d}", float(self.rng.choice([1, 2, 3, 5, 12, 18])), "GST"
            retired = i >= self.counts.tax_codes - 5  # last five are retired
            rows.append(
                {
                    "record_id": _record_id("TAX", i + 1),
                    "tax_code": code,
                    "description": f"Tax code {code}",
                    "rate": rate,
                    "tax_type": tax_type,
                    "is_retired": str(retired).lower(),
                    "effective_from": "2017-07-01",
                    "effective_to": "2023-03-31" if retired else "",
                }
            )
        return pd.DataFrame(rows)

    # ------------------------------------------------------- master data ----
    def build_suppliers(self) -> pd.DataFrame:
        n = self.counts.suppliers
        rows = []
        for i in range(n):
            city, state, pin = self.rng.choice(_CITIES)
            rows.append(
                {
                    "record_id": _record_id("SUP", i + 1),
                    "supplier_code": f"SUP-{i + 1:05d}",
                    "name": _company_name(self.rng),
                    "gstin": _gstin(self.rng),
                    "pan": _pan(self.rng),
                    "email": f"contact{i}@supplier{i}.example.com",
                    "phone": _phone(self.rng),
                    "pincode": pin,
                    "city": city,
                    "state": state,
                    "country": "IN",
                    "payment_terms": self.rng.choice(_PAYMENT_TERMS),
                    "currency": self.rng.choice(_CURRENCIES),
                    "is_active": "true",
                    "last_activity_date": (
                        self.reference_date - timedelta(days=self.rng.randint(1, 900))
                    ).strftime("%Y-%m-%d"),
                }
            )
        return pd.DataFrame(rows)

    def build_customers(self) -> pd.DataFrame:
        n = self.counts.customers
        rows = []
        for i in range(n):
            city, state, pin = self.rng.choice(_CITIES)
            rows.append(
                {
                    "record_id": _record_id("CUS", i + 1),
                    "customer_code": f"CUS-{i + 1:05d}",
                    "name": _company_name(self.rng),
                    "gstin": _gstin(self.rng),
                    "pan": _pan(self.rng),
                    "email": f"ap{i}@customer{i}.example.com",
                    "phone": _phone(self.rng),
                    "pincode": pin,
                    "city": city,
                    "state": state,
                    "country": "IN",
                    "currency": "INR",
                    "credit_limit": float(self.rng.choice([50_000, 100_000, 250_000, 500_000, 1_000_000])),
                    "is_active": "true",
                    "last_activity_date": (
                        self.reference_date - timedelta(days=self.rng.randint(1, 900))
                    ).strftime("%Y-%m-%d"),
                }
            )
        return pd.DataFrame(rows)

    def build_items(self) -> pd.DataFrame:
        n = self.counts.items
        category_codes = [f"CAT-{i + 1:04d}" for i in range(self.counts.categories)]
        rows = []
        for i in range(n):
            cost = round(self.rng.uniform(5, 5000), 2)
            rows.append(
                {
                    "record_id": _record_id("ITM", i + 1),
                    "item_code": f"ITM-{i + 1:06d}",
                    "name": f"{self.rng.choice(_ITEM_QUALIFIERS)} {self.rng.choice(_ITEM_WORDS)} {i}",
                    "category_code": self.rng.choice(category_codes),
                    "uom_code": self.rng.choice(CANONICAL_UOM),
                    "standard_cost": cost,
                    "list_price": round(cost * self.rng.uniform(1.15, 2.2), 2),
                    "reorder_level": float(self.rng.choice([0, 10, 25, 50, 100])),
                    "is_active": "true",
                    "last_activity_date": (
                        self.reference_date - timedelta(days=self.rng.randint(1, 900))
                    ).strftime("%Y-%m-%d"),
                }
            )
        return pd.DataFrame(rows)

    def build_chart_of_accounts(self) -> pd.DataFrame:
        n = self.counts.chart_of_accounts
        rows = []
        for i in range(n):
            acct_type = _ACCOUNT_TYPES[i % len(_ACCOUNT_TYPES)]
            rows.append(
                {
                    "record_id": _record_id("ACC", i + 1),
                    "account_code": f"{1000 + i}",
                    "account_name": f"{acct_type} account {i}",
                    "account_type": acct_type,
                    "parent_account_code": "" if i < 5 else f"{1000 + (i % 5)}",
                    "currency": "INR",
                    "is_active": "true",
                }
            )
        return pd.DataFrame(rows)

    # ----------------------------------------------------------- finance ----
    def build_gl_balances(self) -> pd.DataFrame:
        n = self.counts.gl_balances
        account_codes = [f"{1000 + i}" for i in range(self.counts.chart_of_accounts)]
        rows = []
        for i in range(n):
            fiscal_year = self.rng.choice([2023, 2024, 2025])
            period = self.rng.randint(1, 12)
            amount = round(self.rng.uniform(1000, 250_000), 2)
            # A balanced pair: one debit line and one credit line per entry.
            rows.append(
                {
                    "record_id": _record_id("GLB", i + 1),
                    "account_code": self.rng.choice(account_codes),
                    "fiscal_year": fiscal_year,
                    "period": period,
                    "debit": amount,
                    "credit": 0.0,
                    "currency": "INR",
                }
            )
        # Mirror each debit with an equal credit on a different account so the
        # clean dataset balances by construction.
        credits = rows[: n // 2]
        mirrored = []
        for i, row in enumerate(credits):
            mirrored.append(
                {
                    "record_id": _record_id("GLB", n + i + 1),
                    "account_code": self.rng.choice(account_codes),
                    "fiscal_year": row["fiscal_year"],
                    "period": row["period"],
                    "debit": 0.0,
                    "credit": row["debit"],
                    "currency": "INR",
                }
            )
        return pd.DataFrame(rows + mirrored)

    def build_purchase_orders(self) -> pd.DataFrame:
        n = self.counts.purchase_orders
        supplier_codes = [f"SUP-{i + 1:05d}" for i in range(self.counts.suppliers)]
        rows = []
        for i in range(n):
            order_date = self.reference_date - timedelta(days=self.rng.randint(1, 1200))
            rows.append(
                {
                    "record_id": _record_id("PO", i + 1),
                    "po_number": f"PO-{i + 1:06d}",
                    "supplier_code": self.rng.choice(supplier_codes),
                    "order_date": order_date.strftime("%Y-%m-%d"),
                    "currency": "INR",
                    "total_amount": round(self.rng.uniform(500, 900_000), 2),
                    "status": self.rng.choice(["open", "open", "open", "closed"]),
                }
            )
        return pd.DataFrame(rows)

    def build_invoices(self) -> pd.DataFrame:
        n = self.counts.invoices
        customer_codes = [f"CUS-{i + 1:05d}" for i in range(self.counts.customers)]
        tax_codes = [f"GST{r}" for r in (5, 12, 18, 28)] + ["EXEMPT", "ZERO"]
        rows = []
        for i in range(n):
            invoice_date = self.reference_date - timedelta(days=self.rng.randint(1, 1500))
            subtotal = round(self.rng.uniform(500, 750_000), 2)
            tax_amount = round(subtotal * 0.18, 2)
            rows.append(
                {
                    "record_id": _record_id("INV", i + 1),
                    "invoice_number": f"INV-{i + 1:07d}",
                    "customer_code": self.rng.choice(customer_codes),
                    "invoice_date": invoice_date.strftime("%Y-%m-%d"),
                    "due_date": (invoice_date + timedelta(days=30)).strftime("%Y-%m-%d"),
                    "currency": "INR",
                    "subtotal": subtotal,
                    "tax_amount": tax_amount,
                    "total_amount": round(subtotal + tax_amount, 2),
                    "tax_code": self.rng.choice(tax_codes),
                    "status": self.rng.choice(["open", "open", "paid", "overdue"]),
                }
            )
        return pd.DataFrame(rows)

    def build_inventory_stock(self) -> pd.DataFrame:
        n = self.counts.inventory_stock
        item_codes = [f"ITM-{i + 1:06d}" for i in range(self.counts.items)]
        rows = []
        for i in range(n):
            rows.append(
                {
                    "record_id": _record_id("STK", i + 1),
                    "item_code": self.rng.choice(item_codes),
                    "warehouse_code": self.rng.choice(["WH01", "WH02", "WH03"]),
                    "uom_code": self.rng.choice(CANONICAL_UOM),
                    "qty_on_hand": float(self.rng.randint(1, 5000)),
                }
            )
        return pd.DataFrame(rows)

    def build_supplier_catalog(self) -> pd.DataFrame:
        n = self.counts.supplier_catalog
        supplier_codes = [f"SUP-{i + 1:05d}" for i in range(self.counts.suppliers)]
        item_codes = [f"ITM-{i + 1:06d}" for i in range(self.counts.items)]
        rows = []
        for i in range(n):
            rows.append(
                {
                    "record_id": _record_id("SCA", i + 1),
                    "supplier_code": self.rng.choice(supplier_codes),
                    "item_code": self.rng.choice(item_codes),
                    "supplier_sku": f"SKU-{i + 1:06d}",
                    "unit_price": round(self.rng.uniform(5, 6000), 2),
                    "currency": "INR",
                    "lead_time_days": self.rng.randint(1, 60),
                    "min_order_qty": float(self.rng.choice([1, 5, 10, 25, 100])),
                }
            )
        return pd.DataFrame(rows)

    def build_employees_payroll(self) -> pd.DataFrame:
        n = self.counts.employees_payroll_summary
        rows = []
        for i in range(n):
            gross = float(self.rng.randint(300_000, 2_500_000))
            rows.append(
                {
                    "record_id": _record_id("EMP", i + 1),
                    "employee_code": f"EMP-{i + 1:05d}",
                    "employee_name": f"{self.rng.choice(_NAME_PREFIX)} Employee {i}",
                    "department": self.rng.choice(_DEPARTMENTS),
                    "fiscal_year": 2025,
                    "gross_salary": gross,
                    "net_salary": round(gross * 0.72, 2),
                    "currency": "INR",
                }
            )
        return pd.DataFrame(rows)

    # --------------------------------------------------------- injectors ----
    def _inject_duplicate_exact(self, df: pd.DataFrame, entity: str, key: str, prefix: str) -> pd.DataFrame:
        """Copy whole rows so the primary key is genuinely violated."""
        if len(df) < self.plan.duplicate_exact:
            return df
        idx = self._sample_indices(len(df), self.plan.duplicate_exact)
        dupes = df.iloc[idx].copy()
        for pos, (_, row) in zip(idx, dupes.iterrows()):
            original_id = row["record_id"]
            self._add_defect(
                "duplicate_exact", entity, original_id, key, "MD001", "blocker", True,
                f"Exact duplicate of {row[key]} (also present as {original_id}).",
            )
        dupes["record_id"] = [
            _record_id(prefix + "D", i + 1) for i in range(len(dupes))
        ]
        return pd.concat([df, dupes], ignore_index=True)

    def _inject_duplicate_fuzzy(self, df: pd.DataFrame, entity: str, key: str) -> pd.DataFrame:
        """New key, near-identical name: a merge candidate, not a hard clash."""
        if len(df) < self.plan.duplicate_fuzzy:
            return df
        idx = self._sample_indices(len(df), self.plan.duplicate_fuzzy)
        new_rows = []
        for n, pos in enumerate(idx):
            row = df.iloc[pos].to_dict()
            original_id = row["record_id"]
            row["name"] = _fuzzy_variant(self.rng, str(row["name"]))
            new_id = _record_id("FUZ", n + 1)
            row["record_id"] = new_id
            # Give it a distinct code so it is a merge candidate, not a clash.
            row[key] = f"{row[key]}-DUP{n + 1}"
            if "gstin" in row:
                row["gstin"] = _gstin(self.rng)
            if "email" in row:
                row["email"] = f"dup{n + 1}@{entity}.example.com"
            new_rows.append(row)
            self._add_defect(
                "duplicate_fuzzy", entity, new_id, "name", "MD002", "warn", True,
                f"Near-duplicate of {original_id} ({original_id} vs {new_id}); "
                "names match fuzzily above threshold.",
            )
        return pd.concat([df, pd.DataFrame(new_rows)], ignore_index=True)

    def _inject_missing_mandatory(self, df: pd.DataFrame, entity: str, fields: list[str]) -> pd.DataFrame:
        idx = self._sample_indices(len(df), self.plan.missing_mandatory)
        for pos in idx:
            fld = self.rng.choice(fields)
            df.iat[pos, df.columns.get_loc(fld)] = ""
            self._add_defect(
                "missing_mandatory", entity, str(df.iat[pos, df.columns.get_loc("record_id")]),
                fld, "MD003", "blocker", False,
                f"Mandatory field {fld} is empty.",
            )
        return df

    def _inject_bad_tax_ids(self, df: pd.DataFrame, entity: str) -> pd.DataFrame:
        idx = self._sample_indices(len(df), self.plan.invalid_gstin)
        bad = ["27AAPFU0939F1Z", "INVALIDGSTIN12345", "29AABCU9603R1Z", "000000000000000", "27AAPFU0939F1ZV"]
        for pos in idx:
            value = self.rng.choice(bad)
            df.iat[pos, df.columns.get_loc("gstin")] = value
            self._add_defect(
                "invalid_gstin", entity, str(df.iat[pos, df.columns.get_loc("record_id")]),
                "gstin", "MD004", "blocker", False,
                f"GSTIN {value!r} fails the 15-character checksum format.",
            )
        idx = self._sample_indices(len(df), self.plan.invalid_pan)
        for pos in idx:
            value = self.rng.choice(["ABCDE1234", "ABCD12345F", "12345ABCDE6", "ABCDE1234F9"])
            df.iat[pos, df.columns.get_loc("pan")] = value
            self._add_defect(
                "invalid_pan", entity, str(df.iat[pos, df.columns.get_loc("record_id")]),
                "pan", "MD005", "warn", False,
                f"PAN {value!r} does not match ABCDE1234F.",
            )
        return df

    def _inject_bad_contacts(self, df: pd.DataFrame, entity: str) -> pd.DataFrame:
        idx = self._sample_indices(len(df), self.plan.invalid_email)
        for pos in idx:
            value = self.rng.choice(
                ["not-an-email", "user@@example.com", "missing-at-sign.com", "user@", "@example.com"]
            )
            df.iat[pos, df.columns.get_loc("email")] = value
            self._add_defect(
                "invalid_email", entity, str(df.iat[pos, df.columns.get_loc("record_id")]),
                "email", "MD006", "warn", False, f"Email {value!r} is not a valid address.",
            )
        idx = self._sample_indices(len(df), self.plan.invalid_pincode)
        for pos in idx:
            value = self.rng.choice(["12345", "9999999", "ABCDEF", "000000", "40001"])
            df.iat[pos, df.columns.get_loc("pincode")] = value
            self._add_defect(
                "invalid_pincode", entity, str(df.iat[pos, df.columns.get_loc("record_id")]),
                "pincode", "MD007", "warn", False,
                f"Pincode {value!r} is not a 6-digit Indian postcode.",
            )
        idx = self._sample_indices(len(df), self.plan.invalid_phone)
        for pos in idx:
            value = self.rng.choice(["12345", "+91-abc-def", "0000000000", "+9199999999999999"])
            df.iat[pos, df.columns.get_loc("phone")] = value
            self._add_defect(
                "invalid_phone", entity, str(df.iat[pos, df.columns.get_loc("record_id")]),
                "phone", "MD008", "info", False, f"Phone {value!r} is not a valid number.",
            )
        return df

    def _inject_uom_problems(self, items: pd.DataFrame, uom_master: pd.DataFrame) -> pd.DataFrame:
        known = set(uom_master["uom_code"])
        idx = self._sample_indices(len(items), self.plan.uom_variant)
        for pos in idx:
            canonical = str(items.iat[pos, items.columns.get_loc("uom_code")])
            if canonical not in UOM_VARIANTS:
                canonical = "PCS"
            variant = self.rng.choice(UOM_VARIANTS[canonical])
            items.iat[pos, items.columns.get_loc("uom_code")] = variant
            self._add_defect(
                "uom_variant", "items", str(items.iat[pos, items.columns.get_loc("record_id")]),
                "uom_code", "MD010", "warn", True,
                f"Unit {variant!r} is a variant of {canonical!r}; normalise to {canonical}.",
            )
        idx = self._sample_indices(len(items), self.plan.uom_unknown)
        for pos in idx:
            value = self.rng.choice(["XZ9", "??", "UNKNOWN", "N/A"])
            items.iat[pos, items.columns.get_loc("uom_code")] = value
            self._add_defect(
                "uom_unknown", "items", str(items.iat[pos, items.columns.get_loc("record_id")]),
                "uom_code", "MD009", "blocker", False,
                f"Unit {value!r} does not exist in the UoM master ({len(known)} codes).",
            )
        return items

    def _inject_stale_records(self, df: pd.DataFrame, entity: str) -> pd.DataFrame:
        idx = self._sample_indices(len(df), self.plan.stale_record)
        cutoff = self.reference_date - timedelta(days=365 * 3)
        for pos in idx:
            when = cutoff - timedelta(days=self.rng.randint(1, 1500))
            df.iat[pos, df.columns.get_loc("last_activity_date")] = when.strftime("%Y-%m-%d")
            df.iat[pos, df.columns.get_loc("is_active")] = "true"
            self._add_defect(
                "stale_record", entity, str(df.iat[pos, df.columns.get_loc("record_id")]),
                "last_activity_date", "MD011", "warn", True,
                f"No activity since {when.isoformat()}; archive rather than migrate.",
            )
        return df

    def _inject_whitespace_case(self, df: pd.DataFrame, entity: str, fields: list[str]) -> pd.DataFrame:
        idx = self._sample_indices(len(df), self.plan.whitespace_case)
        for pos in idx:
            fld = self.rng.choice(fields)
            current = str(df.iat[pos, df.columns.get_loc(fld)])
            style = self.rng.choice(["pad", "upper", "lower", "double"])
            if style == "pad":
                new = f"  {current} "
            elif style == "upper":
                new = current.upper()
            elif style == "lower":
                new = current.lower()
            else:
                new = current.replace(" ", "  ")
            df.iat[pos, df.columns.get_loc(fld)] = new
            self._add_defect(
                "whitespace_case", entity, str(df.iat[pos, df.columns.get_loc("record_id")]),
                fld, "MD012", "info", True,
                f"{fld} has stray whitespace or inconsistent casing ({style}).",
            )
        return df

    def _inject_date_formats(self, df: pd.DataFrame, entity: str, fields: list[str]) -> pd.DataFrame:
        idx = self._sample_indices(len(df), self.plan.date_format)
        for pos in idx:
            fld = self.rng.choice(fields)
            raw = str(df.iat[pos, df.columns.get_loc(fld)])
            try:
                parsed = date.fromisoformat(raw)
            except ValueError:
                continue
            rendered = _mixed_date(self.rng, parsed)
            if rendered == raw:
                continue
            df.iat[pos, df.columns.get_loc(fld)] = rendered
            self._add_defect(
                "date_format", entity, str(df.iat[pos, df.columns.get_loc("record_id")]),
                fld, "MD013", "info", True,
                f"{fld} uses a non-ISO format ({rendered!r}); normalise to ISO 8601.",
            )
        return df

    def _inject_orphans(
        self,
        child: pd.DataFrame,
        entity: str,
        fk_column: str,
        valid_keys: set[str],
    ) -> pd.DataFrame:
        n = {
            "invoices": self.plan.orphan_customer,
            "purchase_orders": self.plan.orphan_supplier,
            "items": self.plan.orphan_category,
            "gl_balances": self.plan.orphan_account,
        }[entity]
        idx = self._sample_indices(len(child), n)
        rule = {
            "invoices": "REF001",
            "purchase_orders": "REF002",
            "items": "REF003",
            "gl_balances": "REF004",
        }[entity]
        for pos in idx:
            missing = f"MISSING-{self.rng.randint(90000, 99999)}"
            child.iat[pos, child.columns.get_loc(fk_column)] = missing
            self._add_defect(
                "orphan", entity, str(child.iat[pos, child.columns.get_loc("record_id")]),
                fk_column, rule, "blocker", False,
                f"{fk_column}={missing!r} has no matching parent record.",
            )
        return child

    def _inject_unbalanced_ledger(self, gl: pd.DataFrame) -> pd.DataFrame:
        """Break the debit/credit balance for a set of (year, period) buckets."""
        buckets = gl.groupby(["fiscal_year", "period"]).size()
        targets = self._pick(list(buckets.index), self.plan.unbalanced_ledger)
        new_rows = []
        for n, (year, period) in enumerate(targets):
            amount = round(self.rng.uniform(10_000, 90_000), 2)
            rid = _record_id("GLU", n + 1)
            new_rows.append(
                {
                    "record_id": rid,
                    "account_code": f"{1000 + self.rng.randint(0, 50)}",
                    "fiscal_year": int(year),
                    "period": int(period),
                    "debit": amount,
                    "credit": 0.0,
                    "currency": "INR",
                }
            )
            self._add_defect(
                "unbalanced_ledger", "gl_balances", rid, "debit", "FIN001", "blocker", False,
                f"Fiscal {year} period {period} has an unmatched debit of {amount}; "
                "trial balance does not balance.",
            )
        return pd.concat([gl, pd.DataFrame(new_rows)], ignore_index=True)

    def _inject_retired_tax_codes(self, invoices: pd.DataFrame, tax: pd.DataFrame) -> pd.DataFrame:
        retired = tax.loc[tax["is_retired"] == "true", "tax_code"].tolist()
        if not retired:
            return invoices
        idx = self._sample_indices(len(invoices), self.plan.retired_tax_code)
        for pos in idx:
            code = self.rng.choice(retired)
            invoices.iat[pos, invoices.columns.get_loc("tax_code")] = code
            self._add_defect(
                "retired_tax_code", "invoices",
                str(invoices.iat[pos, invoices.columns.get_loc("record_id")]),
                "tax_code", "FIN002", "blocker", True,
                f"Tax code {code!r} is retired and must not be used on new documents.",
            )
        return invoices

    def _inject_currency_mismatch(self, invoices: pd.DataFrame, customers: pd.DataFrame) -> pd.DataFrame:
        idx = self._sample_indices(len(invoices), self.plan.currency_mismatch)
        for pos in idx:
            invoices.iat[pos, invoices.columns.get_loc("currency")] = self.rng.choice(["USD", "EUR"])
            self._add_defect(
                "currency_mismatch", "invoices",
                str(invoices.iat[pos, invoices.columns.get_loc("record_id")]),
                "currency", "FIN003", "warn", False,
                "Invoice currency differs from the customer's ledger currency.",
            )
        return invoices

    def _inject_negative_stock(self, stock: pd.DataFrame) -> pd.DataFrame:
        idx = self._sample_indices(len(stock), self.plan.negative_stock)
        for pos in idx:
            value = -float(self.rng.randint(1, 500))
            stock.iat[pos, stock.columns.get_loc("qty_on_hand")] = value
            self._add_defect(
                "negative_stock", "inventory_stock",
                str(stock.iat[pos, stock.columns.get_loc("record_id")]),
                "qty_on_hand", "FIN004", "blocker", False,
                f"Quantity on hand is negative ({value}); physically impossible.",
            )
        return stock

    def _inject_impossible_dates(self, invoices: pd.DataFrame) -> pd.DataFrame:
        idx = self._sample_indices(len(invoices), self.plan.impossible_date)
        for n, pos in enumerate(idx):
            mode = n % 3
            if mode == 0:
                value = (self.reference_date + timedelta(days=self.rng.randint(30, 900))).isoformat()
                field = "invoice_date"
                why = "invoice date is in the future"
            elif mode == 1:
                value = "1899-12-31"
                field = "invoice_date"
                why = "invoice date predates the company"
            else:
                value = (self.reference_date - timedelta(days=self.rng.randint(500, 1500))).isoformat()
                field = "due_date"
                why = "due date falls before the invoice date"
            invoices.iat[pos, invoices.columns.get_loc(field)] = value
            self._add_defect(
                "impossible_date", "invoices",
                str(invoices.iat[pos, invoices.columns.get_loc("record_id")]),
                field, "FIN005", "blocker", False, f"Impossible date: {why} ({value}).",
            )
        return invoices

    def _inject_duplicate_invoices(self, invoices: pd.DataFrame) -> pd.DataFrame:
        idx = self._sample_indices(len(invoices), self.plan.duplicate_invoice)
        new_rows = []
        for n, pos in enumerate(idx):
            row = invoices.iloc[pos].to_dict()
            original = row["invoice_number"]
            row["record_id"] = _record_id("INVD", n + 1)
            new_rows.append(row)
            self._add_defect(
                "duplicate_invoice", "invoices", _record_id("INVD", n + 1),
                "invoice_number", "FIN006", "blocker", True,
                f"Invoice number {original!r} appears more than once.",
            )
        return pd.concat([invoices, pd.DataFrame(new_rows)], ignore_index=True)

    def _inject_salary_outliers(self, payroll: pd.DataFrame) -> pd.DataFrame:
        idx = self._sample_indices(len(payroll), self.plan.salary_outlier)
        for pos in idx:
            normal = float(payroll.iat[pos, payroll.columns.get_loc("gross_salary")])
            outlier = round(normal * self.rng.uniform(8, 25), 2)
            payroll.iat[pos, payroll.columns.get_loc("gross_salary")] = outlier
            payroll.iat[pos, payroll.columns.get_loc("net_salary")] = round(outlier * 0.72, 2)
            self._add_defect(
                "salary_outlier", "employees_payroll_summary",
                str(payroll.iat[pos, payroll.columns.get_loc("record_id")]),
                "gross_salary", "FIN007", "warn", False,
                f"Gross salary {outlier} is far above the departmental norm ({normal}).",
            )
        return payroll

    def _inject_negative_amounts(self, invoices: pd.DataFrame) -> pd.DataFrame:
        idx = self._sample_indices(len(invoices), self.plan.negative_amount)
        for pos in idx:
            amount = -round(self.rng.uniform(500, 50_000), 2)
            invoices.iat[pos, invoices.columns.get_loc("total_amount")] = amount
            self._add_defect(
                "negative_amount", "invoices",
                str(invoices.iat[pos, invoices.columns.get_loc("record_id")]),
                "total_amount", "FIN008", "warn", False,
                f"Invoice total is negative ({amount}) without a credit-note flag.",
            )
        return invoices

    # ------------------------------------------------------------- driver ---
    def generate(self) -> DemoDataset:
        logger.info("generating demo data with seed=%s", self.seed)

        categories = self.build_categories()
        uom = self.build_uom()
        tax = self.build_tax_codes()
        suppliers = self.build_suppliers()
        customers = self.build_customers()
        items = self.build_items()
        accounts = self.build_chart_of_accounts()
        gl = self.build_gl_balances()
        pos = self.build_purchase_orders()
        invoices = self.build_invoices()
        stock = self.build_inventory_stock()
        catalog = self.build_supplier_catalog()
        payroll = self.build_employees_payroll()

        # Master data defects.
        for df, entity, key, prefix, name_fields in (
            (suppliers, "suppliers", "supplier_code", "SUPD", ["name", "city", "payment_terms"]),
            (customers, "customers", "customer_code", "CUSD", ["name", "city"]),
        ):
            df = self._inject_duplicate_exact(df, entity, key, prefix)
            df = self._inject_duplicate_fuzzy(df, entity, key)
            df = self._inject_missing_mandatory(df, entity, [key, "name"])
            df = self._inject_bad_tax_ids(df, entity)
            df = self._inject_bad_contacts(df, entity)
            df = self._inject_stale_records(df, entity)
            df = self._inject_whitespace_case(df, entity, name_fields)
            if entity == "suppliers":
                suppliers = df
            else:
                customers = df

        items = self._inject_uom_problems(items, uom)
        items = self._inject_missing_mandatory(items, "items", ["item_code", "name"])
        items = self._inject_whitespace_case(items, "items", ["name"])
        items = self._inject_date_formats(items, "items", ["last_activity_date"])
        items = self._inject_stale_records(items, "items")

        # Referential integrity.
        invoices = self._inject_orphans(
            invoices, "invoices", "customer_code", set(customers["customer_code"])
        )
        pos = self._inject_orphans(
            pos, "purchase_orders", "supplier_code", set(suppliers["supplier_code"])
        )
        items = self._inject_orphans(
            items, "items", "category_code", set(categories["category_code"])
        )
        gl = self._inject_orphans(
            gl, "gl_balances", "account_code", set(accounts["account_code"])
        )

        # Finance defects.
        gl = self._inject_unbalanced_ledger(gl)
        invoices = self._inject_retired_tax_codes(invoices, tax)
        invoices = self._inject_currency_mismatch(invoices, customers)
        invoices = self._inject_impossible_dates(invoices)
        invoices = self._inject_duplicate_invoices(invoices)
        invoices = self._inject_negative_amounts(invoices)
        invoices = self._inject_date_formats(invoices, "invoices", ["invoice_date"])
        invoices = self._inject_missing_mandatory(invoices, "invoices", ["invoice_number"])
        stock = self._inject_negative_stock(stock)
        payroll = self._inject_salary_outliers(payroll)

        self.frames = {
            "categories": categories,
            "uom": uom,
            "tax_codes": tax,
            "suppliers": suppliers,
            "customers": customers,
            "items": items,
            "chart_of_accounts": accounts,
            "gl_balances": gl,
            "purchase_orders": pos,
            "invoices": invoices,
            "inventory_stock": stock,
            "supplier_catalog": catalog,
            "employees_payroll_summary": payroll,
        }

        # Shuffle row order so defects are not clustered at the top of a file.
        for name, frame in self.frames.items():
            self.frames[name] = frame.sample(frac=1.0, random_state=self.seed).reset_index(drop=True)

        dataset = DemoDataset(
            frames=self.frames,
            defects=self.defects,
            seed=self.seed,
            generated_at=self.reference_date.isoformat(),
        )
        logger.info(
            "generated %s records across %s entities with %s labelled defects",
            dataset.total_records,
            len(self.frames),
            len(self.defects),
        )
        return dataset


def generate(
    *,
    seed: int = DEFAULT_SEED,
    counts: RecordCounts | None = None,
    plan: DefectPlan | None = None,
    reference_date: date | None = None,
) -> DemoDataset:
    return DemoDataGenerator(
        seed=seed, counts=counts, plan=plan, reference_date=reference_date
    ).generate()
