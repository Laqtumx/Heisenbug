"""Legacy ERP entity definitions.

This module is the contract between three things:

* the demo data generator, which writes CSV files with these columns;
* the profile/validate/clean stages, which read them;
* the target ERP schema, whose columns each legacy column maps onto.

Each entity declares its columns with a dtype, which columns are mandatory, and
the target column each one loads into. Keeping this in one place means adding a
column is a one-line change rather than an edit in five modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    dtype: str  # string | int | float | date | bool
    mandatory: bool = False
    target: str | None = None
    sensitive: bool = False
    description: str = ""


@dataclass(frozen=True)
class EntitySpec:
    name: str
    target_table: str
    primary_key: str
    columns: tuple[ColumnSpec, ...]
    load_order: int
    depends_on: tuple[str, ...] = ()
    record_id_column: str = "record_id"
    description: str = ""

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    @property
    def mandatory_columns(self) -> list[str]:
        return [c.name for c in self.columns if c.mandatory]

    @property
    def sensitive_columns(self) -> list[str]:
        return [c.name for c in self.columns if c.sensitive]


def _c(name: str, dtype: str, *, mandatory: bool = False, target: str | None = None,
       sensitive: bool = False, description: str = "") -> ColumnSpec:
    return ColumnSpec(name, dtype, mandatory, target, sensitive, description)


# The record_id column is a legacy artefact: it exists so every defect the
# generator injects can be traced to a single source row in the demo.
RECORD_ID = _c("record_id", "string", mandatory=True, target="source_ref")


CATEGORIES = EntitySpec(
    name="categories",
    target_table="categories",
    primary_key="category_code",
    load_order=1,
    description="Product category tree.",
    columns=(
        RECORD_ID,
        _c("category_code", "string", mandatory=True, target="category_code"),
        _c("name", "string", mandatory=True, target="name"),
        _c("parent_code", "string", target="parent_code"),
        _c("is_active", "bool", target="is_active"),
    ),
)

UOM = EntitySpec(
    name="uom",
    target_table="uom",
    primary_key="uom_code",
    load_order=2,
    description="Units of measure and their conversion factors.",
    columns=(
        RECORD_ID,
        _c("uom_code", "string", mandatory=True, target="uom_code"),
        _c("name", "string", mandatory=True, target="name"),
        _c("base_uom_code", "string", target="base_uom_code"),
        _c("conversion_factor", "float", target="conversion_factor"),
    ),
)

TAX_CODES = EntitySpec(
    name="tax_codes",
    target_table="tax_codes",
    primary_key="tax_code",
    load_order=3,
    description="Tax codes, including retired ones that must not be migrated.",
    columns=(
        RECORD_ID,
        _c("tax_code", "string", mandatory=True, target="tax_code"),
        _c("description", "string", target="description"),
        _c("rate", "float", target="rate"),
        _c("tax_type", "string", target="tax_type"),
        _c("is_retired", "bool", target="is_retired"),
        _c("effective_from", "date", target="effective_from"),
        _c("effective_to", "date", target="effective_to"),
    ),
)

SUPPLIERS = EntitySpec(
    name="suppliers",
    target_table="suppliers",
    primary_key="supplier_code",
    load_order=4,
    description="Vendor master, including dead vendors to archive.",
    columns=(
        RECORD_ID,
        _c("supplier_code", "string", mandatory=True, target="supplier_code"),
        _c("name", "string", mandatory=True, target="name"),
        _c("gstin", "string", target="gstin", sensitive=True,
           description="15-character Indian GST number."),
        _c("pan", "string", target="pan", sensitive=True),
        _c("email", "string", target="email", sensitive=True),
        _c("phone", "string", target="phone", sensitive=True),
        _c("pincode", "string", target="pincode"),
        _c("city", "string", target="city"),
        _c("state", "string", target="state"),
        _c("country", "string", target="country"),
        _c("payment_terms", "string", target="payment_terms"),
        _c("currency", "string", target="currency"),
        _c("is_active", "bool", target="is_active"),
        _c("last_activity_date", "date", target="last_activity_date"),
    ),
)

CUSTOMERS = EntitySpec(
    name="customers",
    target_table="customers",
    primary_key="customer_code",
    load_order=5,
    description="Customer master with credit limits and tax identifiers.",
    columns=(
        RECORD_ID,
        _c("customer_code", "string", mandatory=True, target="customer_code"),
        _c("name", "string", mandatory=True, target="name"),
        _c("gstin", "string", target="gstin", sensitive=True),
        _c("pan", "string", target="pan", sensitive=True),
        _c("email", "string", target="email", sensitive=True),
        _c("phone", "string", target="phone", sensitive=True),
        _c("pincode", "string", target="pincode"),
        _c("city", "string", target="city"),
        _c("state", "string", target="state"),
        _c("country", "string", target="country"),
        _c("currency", "string", target="currency"),
        _c("credit_limit", "float", target="credit_limit"),
        _c("is_active", "bool", target="is_active"),
        _c("last_activity_date", "date", target="last_activity_date"),
    ),
)

ITEMS = EntitySpec(
    name="items",
    target_table="items",
    primary_key="item_code",
    load_order=6,
    depends_on=("categories", "uom"),
    description="Item master. Carries the orphan-category and mixed-UoM defects.",
    columns=(
        RECORD_ID,
        _c("item_code", "string", mandatory=True, target="item_code"),
        _c("name", "string", mandatory=True, target="name"),
        _c("category_code", "string", target="category_code"),
        _c("uom_code", "string", target="uom_code"),
        _c("standard_cost", "float", target="standard_cost"),
        _c("list_price", "float", target="list_price"),
        _c("reorder_level", "float", target="reorder_level"),
        _c("is_active", "bool", target="is_active"),
        _c("last_activity_date", "date", target="last_activity_date"),
    ),
)

CHART_OF_ACCOUNTS = EntitySpec(
    name="chart_of_accounts",
    target_table="chart_of_accounts",
    primary_key="account_code",
    load_order=7,
    description="General ledger account hierarchy.",
    columns=(
        RECORD_ID,
        _c("account_code", "string", mandatory=True, target="account_code"),
        _c("account_name", "string", mandatory=True, target="account_name"),
        _c("account_type", "string", mandatory=True, target="account_type"),
        _c("parent_account_code", "string", target="parent_account_code"),
        _c("currency", "string", target="currency"),
        _c("is_active", "bool", target="is_active"),
    ),
)

GL_BALANCES = EntitySpec(
    name="gl_balances",
    target_table="gl_balances",
    primary_key="record_id",
    load_order=8,
    depends_on=("chart_of_accounts",),
    description="Trial balance lines. The source of the debits-equals-credits check.",
    columns=(
        RECORD_ID,
        _c("account_code", "string", mandatory=True, target="account_code"),
        _c("fiscal_year", "int", mandatory=True, target="fiscal_year"),
        _c("period", "int", mandatory=True, target="period"),
        _c("debit", "float", target="debit"),
        _c("credit", "float", target="credit"),
        _c("currency", "string", target="currency"),
    ),
)

PURCHASE_ORDERS = EntitySpec(
    name="purchase_orders",
    target_table="purchase_orders",
    primary_key="po_number",
    load_order=9,
    depends_on=("suppliers",),
    description="Open purchase orders. Carries supplier-orphan defects.",
    columns=(
        RECORD_ID,
        _c("po_number", "string", mandatory=True, target="po_number"),
        _c("supplier_code", "string", target="supplier_code"),
        _c("order_date", "date", target="order_date"),
        _c("currency", "string", target="currency"),
        _c("total_amount", "float", target="total_amount"),
        _c("status", "string", target="status"),
    ),
)

INVOICES = EntitySpec(
    name="invoices",
    target_table="invoices",
    primary_key="invoice_number",
    load_order=10,
    depends_on=("customers", "tax_codes"),
    description="Sales invoices. Carries customer-orphan and duplicate-number defects.",
    columns=(
        RECORD_ID,
        _c("invoice_number", "string", mandatory=True, target="invoice_number"),
        _c("customer_code", "string", target="customer_code"),
        _c("invoice_date", "date", target="invoice_date"),
        _c("due_date", "date", target="due_date"),
        _c("currency", "string", target="currency"),
        _c("subtotal", "float", target="subtotal"),
        _c("tax_amount", "float", target="tax_amount"),
        _c("total_amount", "float", target="total_amount"),
        _c("tax_code", "string", target="tax_code"),
        _c("status", "string", target="status"),
    ),
)

INVENTORY_STOCK = EntitySpec(
    name="inventory_stock",
    target_table="inventory_stock",
    primary_key="record_id",
    load_order=11,
    depends_on=("items", "uom"),
    description="Stock on hand. Carries negative-stock defects.",
    columns=(
        RECORD_ID,
        _c("item_code", "string", mandatory=True, target="item_code"),
        _c("warehouse_code", "string", target="warehouse_code"),
        _c("uom_code", "string", target="uom_code"),
        _c("qty_on_hand", "float", target="qty_on_hand"),
    ),
)

SUPPLIER_CATALOG = EntitySpec(
    name="supplier_catalog",
    target_table="supplier_catalog",
    primary_key="record_id",
    load_order=12,
    depends_on=("suppliers", "items"),
    description="Which items each supplier can quote for.",
    columns=(
        RECORD_ID,
        _c("supplier_code", "string", mandatory=True, target="supplier_code"),
        _c("item_code", "string", mandatory=True, target="item_code"),
        _c("supplier_sku", "string", target="supplier_sku"),
        _c("unit_price", "float", target="unit_price"),
        _c("currency", "string", target="currency"),
        _c("lead_time_days", "int", target="lead_time_days"),
        _c("min_order_qty", "float", target="min_order_qty"),
    ),
)

EMPLOYEES_PAYROLL = EntitySpec(
    name="employees_payroll_summary",
    target_table="employees_payroll_summary",
    primary_key="record_id",
    load_order=13,
    description="Payroll summary. Salary columns are encrypted and masked by role.",
    columns=(
        RECORD_ID,
        _c("employee_code", "string", mandatory=True, target="employee_code"),
        _c("employee_name", "string", mandatory=True, target="employee_name"),
        _c("department", "string", target="department"),
        _c("fiscal_year", "int", target="fiscal_year"),
        _c("gross_salary", "float", target="gross_salary", sensitive=True),
        _c("net_salary", "float", target="net_salary", sensitive=True),
        _c("currency", "string", target="currency"),
    ),
)


ENTITIES: tuple[EntitySpec, ...] = (
    CATEGORIES,
    UOM,
    TAX_CODES,
    SUPPLIERS,
    CUSTOMERS,
    ITEMS,
    CHART_OF_ACCOUNTS,
    GL_BALANCES,
    PURCHASE_ORDERS,
    INVOICES,
    INVENTORY_STOCK,
    SUPPLIER_CATALOG,
    EMPLOYEES_PAYROLL,
)

ENTITIES_BY_NAME: dict[str, EntitySpec] = {e.name: e for e in ENTITIES}

# Load order is derived from the dependency graph so it cannot drift.
LOAD_ORDER: tuple[str, ...] = tuple(
    e.name for e in sorted(ENTITIES, key=lambda x: x.load_order)
)


def dependency_order() -> list[str]:
    """Topologically sorted entity names, stable within the same rank."""
    remaining = {e.name: set(e.depends_on) for e in ENTITIES}
    ordered: list[str] = []
    while remaining:
        ready = sorted(
            (name for name, deps in remaining.items() if not deps),
            key=lambda n: ENTITIES_BY_NAME[n].load_order,
        )
        if not ready:
            raise ValueError(f"Cyclic dependency detected among: {sorted(remaining)}")
        for name in ready:
            ordered.append(name)
            del remaining[name]
        for deps in remaining.values():
            deps.difference_update(ready)
    return ordered
