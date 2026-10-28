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
import string
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from app.config import settings
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
# A second name word keeps the generated pool far larger than the record count,
# so a clean dataset contains no accidental near-duplicate names. Without it,
# random generation alone would create thousands of fuzzy matches that are not
# injected defects and would make the detection rate unmeasurable.
_NAME_BRAND = [
    "Alpha", "Beta", "Gamma", "Delta", "Omega", "Sigma", "Vertex", "Prime",
    "Nova", "Orion", "Atlas", "Titan", "Zenith", "Apex", "Nexus", "Quantum",
    "Falcon", "Griffin", "Phoenix", "Dragon", "Cobalt", "Onyx", "Jade", "Amber",
    "Crystal", "Granite", "Basalt", "Copper", "Bronze", "Silver", "Crimson",
    "Indigo", "Sapphire", "Emerald", "Topaz", "Opal", "Quartz", "Cedar", "Maple",
    "Birch", "Willow", "Aspen", "Larch", "Alder", "Hazel", "Rowan", "Yew",
    "Borealis", "Meridian", "Solstice", "Equinox", "Horizon", "Vantage", "Crest",
    "Summit", "Ridge", "Valley", "Harbour", "Anchor", "Compass", "Beacon",
]
_NAME_SUFFIX = [
    "Industries", "Traders", "Enterprises", "Solutions", "Logistics", "Textiles",
    "Engineering", "Polymers", "Metals", "Electricals", "Packaging", "Chemicals",
]
# Every legal form is already title case, so the title-case rule does not flag
# clean records. "and Sons" was removed for that reason, and "Industries" is
# absent because it also appears in the suffix pool: a name ending in
# "Industries Industries" becomes "Inds Inds" under the abbreviation defect,
# which drops the similarity score below the fuzzy threshold.
_LEGAL_FORM = ["Pvt Ltd", "Ltd", "LLP", "Inc", "Sons", "Private Limited"]
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

# Canonical units. The messy variants the generator injects are read from the
# MD010 rule configuration rather than duplicated here, so the defects the
# generator creates and the defects the rule looks for cannot drift apart.
CANONICAL_UOM = ["KG", "PCS", "LTR", "BOX", "MTR", "SET", "PKT", "TON"]


def uom_variants() -> dict[str, list[str]]:
    """Aliases from the MD010 rule, filtered to ones the rule can detect.

    An alias that differs from its canonical code only by surrounding
    whitespace is dropped: the rule strips before matching, so such a value
    would never be flagged. Whitespace problems are MD012's job.
    """
    from app.rules.loader import load_ruleset

    rule = load_ruleset().by_id("MD010")
    if rule is None:
        return {}
    variants = rule.params.get("variants", {})
    return {
        canonical: [
            alias
            for alias in aliases
            if alias.strip().lower() != canonical.strip().lower()
        ]
        for canonical, aliases in variants.items()
    }

_ACCOUNT_TYPES = ["Asset", "Liability", "Equity", "Revenue", "Expense"]

# Date formats deliberately mixed so normalisation has something to fix.
_DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%d-%b-%Y", "%Y%m%d", "%d.%m.%Y"]


def _record_id(prefix: str, index: int) -> str:
    return f"{prefix}-{index:06d}"


def _gstin(rng: random.Random) -> str:
    """A well-formed GSTIN: 2 state digits, 10-char PAN, entity, Z, checksum.

    The PAN segment is 5 letters, 4 digits, 1 letter. Drawing the letter
    positions from the full alphanumeric alphabet would put digits there and
    produce GSTINs the format rule correctly rejects.
    """
    state = rng.choice([f"{n:02d}" for n in range(1, 38)])
    pan = "".join(rng.choice(string.ascii_uppercase) for _ in range(5))
    pan += "".join(str(rng.randint(0, 9)) for _ in range(4))
    pan += rng.choice(string.ascii_uppercase)
    entity = str(rng.randint(1, 9))
    checksum = rng.choice(_GSTIN_ALPHABET)
    return f"{state}{pan}{entity}Z{checksum}"


def _pan(rng: random.Random) -> str:
    """A structurally valid PAN: 5 letters, 4 digits, 1 letter.

    The fourth character encodes the holder type and the fifth is the first
    letter of the surname, so both must be letters. Drawing from the full
    alphabet would put digits there and make the generator emit records that the
    PAN rule correctly flags — noise that would obscure the injected defects.
    """
    return (
        "".join(rng.choice(string.ascii_uppercase) for _ in range(5))
        + "".join(str(rng.randint(0, 9)) for _ in range(4))
        + rng.choice(string.ascii_uppercase)
    )


def _phone(rng: random.Random) -> str:
    return f"+91{rng.randint(6000000000, 9999999999)}"


def _company_name(rng: random.Random) -> str:
    """A plausible company name."""
    return (
        f"{rng.choice(_NAME_PREFIX)} {rng.choice(_NAME_BRAND)} "
        f"{rng.choice(_NAME_BRAND)} {rng.choice(_NAME_SUFFIX)} {rng.choice(_LEGAL_FORM)}"
    )


# Mixed-radix name pool. The product of these lengths is the cycle length of
# the name sequence, and the divisors below must match it exactly for the
# mapping to be a bijection.
_NAME_RADIX = (
    len(_NAME_PREFIX),
    len(_NAME_BRAND),
    len(_NAME_BRAND),
    len(_NAME_SUFFIX),
    len(_LEGAL_FORM),
)
_NAME_POOL_SIZE = 1
for _radix in _NAME_RADIX:
    _NAME_POOL_SIZE *= _radix


def _unique_name(index: int, rng: random.Random) -> str:
    """A company name that is unique and unrelated to its neighbours.

    Uniqueness alone is not enough. Appending the index, or any scheme where
    consecutive records get consecutive names, leaves neighbouring names 95%
    similar and the fuzzy rule then flags clean records. The name is therefore
    a mixed-radix decode of a scrambled index, so uniqueness holds by
    construction and adjacent records land on unrelated words.
    """
    j = _scramble(index)
    prefix = _NAME_PREFIX[j % len(_NAME_PREFIX)]
    brand_a = _NAME_BRAND[(j // _NAME_RADIX[0]) % len(_NAME_BRAND)]
    brand_b = _NAME_BRAND[(j // (_NAME_RADIX[0] * _NAME_RADIX[1])) % len(_NAME_BRAND)]
    suffix = _NAME_SUFFIX[(j // (_NAME_RADIX[0] * _NAME_RADIX[1] * _NAME_RADIX[2])) % len(_NAME_SUFFIX)]
    legal = _LEGAL_FORM[(j // (_NAME_RADIX[0] * _NAME_RADIX[1] * _NAME_RADIX[2] * _NAME_RADIX[3])) % len(_LEGAL_FORM)]
    return f"{prefix} {brand_a} {brand_b} {suffix} {legal}"


def _scramble(index: int) -> int:
    """A deterministic bijection that spreads consecutive indices far apart.

    Multiplication by a constant coprime with the pool size is a bijection on
    the residues, so no two indices collide and consecutive indices map to
    widely separated values.
    """
    return (index * 2_654_435_761) % _NAME_POOL_SIZE


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
        stale_years: int | None = None,
    ) -> None:
        self.seed = seed
        self.rng = random.Random(seed)
        self.np_rng = np.random.default_rng(seed)
        self.counts = counts or RecordCounts()
        self.plan = plan or DefectPlan()
        self.reference_date = reference_date or date.fromisoformat(settings.reference_date)
        # Kept in step with the validator so the injected staleness boundary is
        # exactly the boundary the rule engine applies.
        self.stale_years = stale_years if stale_years is not None else settings.archive_after_years
        self.defects: list[Defect] = []
        self._defect_seq = 0
        self.frames: dict[str, pd.DataFrame] = {}
        # Row positions already carrying an injected defect for the entity being
        # built. Injectors draw only from the complement, so no row is damaged
        # twice. Without this, one record could be both a duplicate and a bad
        # GSTIN, and the manifest would only record one of the two.
        self._used: set[int] = set()

    # ------------------------------------------------------------- helpers --
    def _reset_used(self) -> None:
        """Row positions are per frame, so the non-overlap set is per entity.

        Keeping one set across entities would make a position in the customer
        file collide with the same position in the supplier file, which is
        meaningless and would silently under-inject.
        """
        self._used = set()

    def _sample_clean(self, n_rows: int, n_defects: int) -> np.ndarray:
        """Distinct, not-yet-damaged row positions. Exact count, never reused."""
        available = np.array(
            [i for i in range(n_rows) if i not in self._used], dtype=int
        )
        n = min(n_defects, len(available))
        if n <= 0:
            return np.array([], dtype=int)
        picked = self.np_rng.choice(available, size=n, replace=False)
        self._used.update(int(i) for i in picked)
        return picked

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
        """Distinct row positions, without the non-overlap guarantee."""
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
        # Extra units so the master has more than the canonical set. Names are
        # already title case so the whitespace/casing rule does not flag them.
        for i in range(len(CANONICAL_UOM), self.counts.uom):
            code = f"UOM{i:02d}"
            rows.append(
                {
                    "record_id": _record_id("UOM", i + 1),
                    "uom_code": code,
                    "name": f"Auxiliary Unit {i}",
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
        # Names are drawn from a unique sequence rather than at random. Random
        # draws collide: with 1,500 records from a 432,000-combination pool,
        # about two pairs per entity land on the same name by chance, and the
        # fuzzy rule then correctly flags two clean records. Assigning each
        # record its own name removes that noise entirely.
        for i in range(n):
            city, state, pin = self.rng.choice(_CITIES)
            rows.append(
                {
                    "record_id": _record_id("SUP", i + 1),
                    "supplier_code": f"SUP-{i + 1:05d}",
                    "name": _unique_name(i, self.rng),
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
                    # Offset so a customer never shares a name with a supplier;
                    # the fuzzy rule compares within one entity, but keeping the
                    # two pools disjoint makes the data easier to reason about.
                    "name": _unique_name(i + 10_000_000, self.rng),
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
        """A trial balance that genuinely balances, period by period.

        Rows are generated as debit/credit pairs of equal amount inside the same
        fiscal period. That makes every period balance by construction, so the
        only unbalanced periods are the ones deliberately injected. Generating
        debits and credits independently, as an earlier version did, leaves every
        period unbalanced and makes the trial balance rule fire on clean data.
        """
        n = self.counts.gl_balances
        account_codes = [f"{1000 + i}" for i in range(self.counts.chart_of_accounts)]
        rows: list[dict] = []
        seq = 0
        for _ in range(n // 2):
            fiscal_year = self.rng.choice([2023, 2024, 2025])
            period = self.rng.randint(1, 12)
            amount = round(self.rng.uniform(1000, 250_000), 2)
            debit_account = self.rng.choice(account_codes)
            credit_account = self.rng.choice(account_codes)
            seq += 1
            rows.append(
                {
                    "record_id": _record_id("GLB", seq),
                    "account_code": debit_account,
                    "fiscal_year": fiscal_year,
                    "period": period,
                    "debit": amount,
                    "credit": 0.0,
                    "currency": "INR",
                }
            )
            seq += 1
            rows.append(
                {
                    "record_id": _record_id("GLB", seq),
                    "account_code": credit_account,
                    "fiscal_year": fiscal_year,
                    "period": period,
                    "debit": 0.0,
                    "credit": amount,
                    "currency": "INR",
                }
            )
        return pd.DataFrame(rows)

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
        # Invoices are dated at least the payment term before the reference
        # date. Dating them later would push the derived due date into the
        # future, which the impossible-date rule correctly flags, leaving
        # unexplained findings in otherwise clean data.
        latest_invoice_date = self.reference_date - timedelta(days=31)
        for i in range(n):
            span = (latest_invoice_date - (self.reference_date - timedelta(days=1500))).days
            invoice_date = latest_invoice_date - timedelta(days=self.rng.randint(0, max(span, 1)))
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
        """Copy whole rows so the business key is genuinely duplicated.

        The defect is recorded against the *copy*, because the copy is the row
        the rule flags: the first occurrence in file order is kept as the
        survivor. Getting this the wrong way round would make recall look poor
        even though the rule found every duplicate.
        """
        if len(df) < self.plan.duplicate_exact:
            return df
        idx = self._sample_clean(len(df), self.plan.duplicate_exact)
        dupes = df.iloc[idx].copy()
        new_ids = [_record_id(prefix + "D", i + 1) for i in range(len(dupes))]
        dupes["record_id"] = new_ids

        for dupe_id, (_, row) in zip(new_ids, dupes.iterrows()):
            self._add_defect(
                "duplicate_exact", entity, dupe_id, key, "MD001", "blocker", True,
                f"Exact duplicate of {row[key]} (copied from {row['record_id']}).",
            )
        return pd.concat([df, dupes], ignore_index=True)

    def _inject_duplicate_fuzzy(self, df: pd.DataFrame, entity: str, key: str) -> pd.DataFrame:
        """New key, near-identical name: a merge candidate, not a hard clash.

        The new key is built by appending a suffix, which makes it sort after
        the original. The rule reports the pair against the larger key, so the
        appended row is the one flagged — the same row the manifest names.
        """
        if len(df) < self.plan.duplicate_fuzzy:
            return df
        idx = self._sample_clean(len(df), self.plan.duplicate_fuzzy)
        new_rows = []
        for n, pos in enumerate(idx):
            row = df.iloc[pos].to_dict()
            original_id = row["record_id"]
            row["name"] = _fuzzy_variant(self.rng, str(row["name"]))
            new_id = _record_id("FUZ", n + 1)
            row["record_id"] = new_id
            # A suffix that sorts after the original key, so the original is
            # always the survivor the rule keeps.
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
        """Blank a mandatory field on some rows.

        Business keys are deliberately not blanked on master entities. A blank
        supplier code would orphan every purchase order that references it, so
        one injected defect would produce hundreds of legitimate but unlabelled
        findings and the detection measurement would lose its meaning.
        """
        idx = self._sample_clean(len(df), self.plan.missing_mandatory)
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
        idx = self._sample_clean(len(df), self.plan.invalid_gstin)
        # Every value here fails the 15-character checksum format. The previous
        # list held two values that were in fact well formed, so the rule was
        # right to accept them and only the manifest was wrong.
        bad = [
            "27AAPFU0939F1Z",     # 14 characters, too short
            "INVALIDGSTIN12345",  # 17 characters, letters in the state digits
            "000000000000000",    # digits where the PAN letters belong
            "GSTIN123",           # far too short
            "ZZAAPFU0939F1Z1",    # state prefix is not numeric
        ]
        for pos in idx:
            value = self.rng.choice(bad)
            df.iat[pos, df.columns.get_loc("gstin")] = value
            self._add_defect(
                "invalid_gstin", entity, str(df.iat[pos, df.columns.get_loc("record_id")]),
                "gstin", "MD004", "blocker", False,
                f"GSTIN {value!r} fails the 15-character checksum format.",
            )
        idx = self._sample_clean(len(df), self.plan.invalid_pan)
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
        idx = self._sample_clean(len(df), self.plan.invalid_email)
        for pos in idx:
            value = self.rng.choice(
                ["not-an-email", "user@@example.com", "missing-at-sign.com", "user@", "@example.com"]
            )
            df.iat[pos, df.columns.get_loc("email")] = value
            self._add_defect(
                "invalid_email", entity, str(df.iat[pos, df.columns.get_loc("record_id")]),
                "email", "MD006", "warn", False, f"Email {value!r} is not a valid address.",
            )
        idx = self._sample_clean(len(df), self.plan.invalid_pincode)
        for pos in idx:
            value = self.rng.choice(["12345", "9999999", "ABCDEF", "000000", "40001"])
            df.iat[pos, df.columns.get_loc("pincode")] = value
            self._add_defect(
                "invalid_pincode", entity, str(df.iat[pos, df.columns.get_loc("record_id")]),
                "pincode", "MD007", "warn", False,
                f"Pincode {value!r} is not a 6-digit Indian postcode.",
            )
        idx = self._sample_clean(len(df), self.plan.invalid_phone)
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
        variants = uom_variants()
        idx = self._sample_clean(len(items), self.plan.uom_variant)
        for pos in idx:
            canonical = str(items.iat[pos, items.columns.get_loc("uom_code")])
            if canonical not in variants or not variants[canonical]:
                canonical = "PCS"
            variant = self.rng.choice(variants[canonical])
            items.iat[pos, items.columns.get_loc("uom_code")] = variant
            self._add_defect(
                "uom_variant", "items", str(items.iat[pos, items.columns.get_loc("record_id")]),
                "uom_code", "MD010", "warn", True,
                f"Unit {variant!r} is a variant of {canonical!r}; normalise to {canonical}.",
            )
        idx = self._sample_clean(len(items), self.plan.uom_unknown)
        for pos in idx:
            # Junk that is clearly not a unit. Values like "N/A" and "?" are
            # excluded on purpose: the profiler treats them as empty, so they
            # would never reach the orphan check and the defect would be
            # undetectable by design.
            value = self.rng.choice(["XZ9", "UNITS", "NOUNIT", "UNDEF", "0", "999"])
            items.iat[pos, items.columns.get_loc("uom_code")] = value
            self._add_defect(
                "uom_unknown", "items", str(items.iat[pos, items.columns.get_loc("record_id")]),
                "uom_code", "MD009", "blocker", False,
                f"Unit {value!r} does not exist in the UoM master ({len(known)} codes).",
            )
        return items

    def _inject_stale_records(self, df: pd.DataFrame, entity: str) -> pd.DataFrame:
        """Push activity dates past the staleness cutoff.

        The cutoff is computed the same way the validator computes it — calendar
        years back from the reference date, not a fixed number of days — so a
        record marked stale here is certain to be flagged as stale there. Using
        an approximate 365-day year would put records near the boundary on the
        wrong side of the line and make recall look worse than it is.
        """
        idx = self._sample_clean(len(df), self.plan.stale_record)
        cutoff = pd.Timestamp(self.reference_date) - pd.DateOffset(
            years=self.stale_years
        )
        for pos in idx:
            # A further 1 to 5 years beyond the cutoff, so every injected record
            # is comfortably inside the stale window.
            when = (cutoff - pd.DateOffset(days=self.rng.randint(30, 5 * 365))).date()
            df.iat[pos, df.columns.get_loc("last_activity_date")] = when.isoformat()
            df.iat[pos, df.columns.get_loc("is_active")] = "true"
            self._add_defect(
                "stale_record", entity, str(df.iat[pos, df.columns.get_loc("record_id")]),
                "last_activity_date", "MD011", "warn", True,
                f"No activity since {when.isoformat()}; archive rather than migrate.",
            )
        return df

    def _inject_whitespace_case(
        self, df: pd.DataFrame, entity: str, fields: list[str], title_fields: list[str]
    ) -> pd.DataFrame:
        """Damage formatting in a way the rule is certain to catch.

        Whitespace problems are injected in any column, but casing problems only
        in columns the rule treats as title case. Injecting a casing change into
        a column the rule does not check for casing would produce a defect that
        no rule is meant to find, and recall would read as a rule failure.
        """
        idx = self._sample_clean(len(df), self.plan.whitespace_case)
        for pos in idx:
            fld = self.rng.choice(fields)
            current = str(df.iat[pos, df.columns.get_loc(fld)])
            styles = ["pad", "double"]
            if fld in title_fields:
                # An all-upper string is how real data writes acronyms, so the
                # rule tolerates it. These styles produce genuine inconsistency.
                styles += ["lower", "mixed"]
            style = self.rng.choice(styles)
            if style == "pad":
                new = f"  {current} "
            elif style == "double":
                new = current.replace(" ", "  ")
            elif style == "lower":
                new = current.lower()
            else:
                new = current.lower().capitalize()
            if new == current:
                continue
            df.iat[pos, df.columns.get_loc(fld)] = new
            self._add_defect(
                "whitespace_case", entity, str(df.iat[pos, df.columns.get_loc("record_id")]),
                fld, "MD012", "info", True,
                f"{fld} has stray whitespace or inconsistent casing ({style}).",
            )
        return df

    def _inject_date_formats(self, df: pd.DataFrame, entity: str, fields: list[str]) -> pd.DataFrame:
        """Render some dates in a non-ISO format.

        A row already carrying an impossible date is skipped: rewriting its
        format would leave the manifest claiming two defects on one record when
        the second injection had overwritten the first.
        """
        idx = self._sample_clean(len(df), self.plan.date_format)
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
        idx = self._sample_clean(len(child), n)
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
        """Break the debit/credit balance for a set of (year, period) buckets.

        One defect per bucket. The trial balance rule reports per bucket, not
        per row, so injecting twice into the same period would produce two
        labelled defects but only one finding, and recall would read as a miss
        when the rule had in fact caught everything.

        The injected debit is larger than any clean line, so the rule attaches
        its finding to this row rather than to an unrelated one in the period.
        """
        buckets = gl.groupby(["fiscal_year", "period"]).size()
        targets = self._pick(list(buckets.index), self.plan.unbalanced_ledger)
        max_clean = 300_000.0
        new_rows = []
        for n, (year, period) in enumerate(targets):
            amount = round(self.rng.uniform(max_clean, max_clean * 2), 2)
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
        idx = self._sample_clean(len(invoices), self.plan.retired_tax_code)
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
        idx = self._sample_clean(len(invoices), self.plan.currency_mismatch)
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
        idx = self._sample_clean(len(stock), self.plan.negative_stock)
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
        idx = self._sample_clean(len(invoices), self.plan.impossible_date)
        for n, pos in enumerate(idx):
            mode = n % 3
            if mode == 0:
                value = (self.reference_date + timedelta(days=self.rng.randint(30, 900))).isoformat()
                field = "invoice_date"
                why = "invoice date is in the future"
                # Move the due date with it. Leaving the due date behind would
                # create a second, unlabelled defect (due before invoice) on the
                # same record.
                invoices.iat[pos, invoices.columns.get_loc("due_date")] = (
                    date.fromisoformat(value) + timedelta(days=30)
                ).isoformat()
            elif mode == 1:
                value = "1899-12-31"
                field = "invoice_date"
                why = "invoice date predates the company"
            else:
                # Derive the due date from this row's own invoice date. Setting
                # it independently, as an earlier version did, sometimes left the
                # due date after the invoice date, so the defect never existed
                # and the miss looked like a rule failure rather than a
                # generator one.
                current = str(invoices.iat[pos, invoices.columns.get_loc("invoice_date")])
                try:
                    invoice_date = date.fromisoformat(current)
                except ValueError:
                    continue
                value = (invoice_date - timedelta(days=30)).isoformat()
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
        idx = self._sample_clean(len(invoices), self.plan.duplicate_invoice)
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
        idx = self._sample_clean(len(payroll), self.plan.salary_outlier)
        for pos in idx:
            normal = float(payroll.iat[pos, payroll.columns.get_loc("gross_salary")])
            # Well above the rule's multiplier. A factor close to the threshold
            # would let the department median drift once several outliers land in
            # the same department, and some injections would stop being outliers.
            outlier = round(normal * self.rng.uniform(20, 40), 2)
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
        idx = self._sample_clean(len(invoices), self.plan.negative_amount)
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
        #
        # Each injector draws from row positions no other injector has touched,
        # so one record never carries two defects. The manifest stays an exact
        # account of the damage, which is what makes the detection rate a real
        # measurement rather than an estimate.
        for df, entity, key, prefix, name_fields in (
            (suppliers, "suppliers", "supplier_code", "SUPD", ["name", "city", "payment_terms"]),
            (customers, "customers", "customer_code", "CUSD", ["name", "city"]),
        ):
            self._reset_used()
            df = self._inject_duplicate_exact(df, entity, key, prefix)
            df = self._inject_duplicate_fuzzy(df, entity, key)
            df = self._inject_bad_tax_ids(df, entity)
            df = self._inject_bad_contacts(df, entity)
            df = self._inject_stale_records(df, entity)
            df = self._inject_whitespace_case(df, entity, name_fields, ["name", "city", "state"])
            # Only non-key mandatory fields are blanked. Blanking a master
            # business key would orphan every child document that references it,
            # turning one labelled defect into hundreds of unlabelled findings.
            df = self._inject_missing_mandatory(df, entity, ["name"])
            if entity == "suppliers":
                suppliers = df
            else:
                customers = df

        self._reset_used()
        items = self._inject_uom_problems(items, uom)
        items = self._inject_whitespace_case(items, "items", ["name"], ["name"])
        items = self._inject_date_formats(items, "items", ["last_activity_date"])
        items = self._inject_stale_records(items, "items")
        items = self._inject_missing_mandatory(items, "items", ["name"])

        # Referential integrity, now that every master frame is final.
        self._reset_used()
        invoices = self._inject_orphans(
            invoices, "invoices", "customer_code", set(customers["customer_code"])
        )
        self._reset_used()
        pos = self._inject_orphans(
            pos, "purchase_orders", "supplier_code", set(suppliers["supplier_code"])
        )
        self._reset_used()
        items = self._inject_orphans(
            items, "items", "category_code", set(categories["category_code"])
        )
        self._reset_used()
        gl = self._inject_orphans(
            gl, "gl_balances", "account_code", set(accounts["account_code"])
        )

        # Finance defects. The non-overlap set is reset here because the block
        # above left it holding gl_balances row positions, which mean nothing
        # for the invoice frame.
        self._reset_used()
        gl = self._inject_unbalanced_ledger(gl)
        invoices = self._inject_retired_tax_codes(invoices, tax)
        invoices = self._inject_currency_mismatch(invoices, customers)
        invoices = self._inject_impossible_dates(invoices)
        invoices = self._inject_date_formats(invoices, "invoices", ["invoice_date"])
        invoices = self._inject_negative_amounts(invoices)
        invoices = self._inject_duplicate_invoices(invoices)
        invoices = self._inject_missing_mandatory(invoices, "invoices", ["invoice_number"])
        self._reset_used()
        stock = self._inject_negative_stock(stock)
        self._reset_used()
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
