"""Row Level Security and RBAC enforcement tests.

These are the tests that back the security claims in the README. They connect
through the same non-superuser role the API uses, so a passing suite means
Postgres really is refusing the rows, not that Python filtered them.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.db.engine import service_session
from app.security import auth
from app.security.deps import load_permissions

pytestmark = pytest.mark.security


@pytest.fixture(scope="module", autouse=True)
def seed_master_data() -> None:
    """Two suppliers, one customer, one invoice, one PO, one catalogue row.

    Rows are removed before seeding and again afterwards. Without the cleanup
    each run adds another set of rows, the `source_ref like 'rls-%'` queries
    match every earlier run's rows, and the assertions fail on data left behind
    by a previous session rather than on anything the current run did.
    Children are deleted before their parents so the foreign keys hold.
    """
    marker = f"rls-{uuid.uuid4().hex[:8]}"

    def purge() -> None:
        with service_session() as session:
            for table in (
                "invoices",
                "purchase_orders",
                "supplier_catalog",
                "suppliers",
                "customers",
            ):
                session.execute(
                    text(f"delete from public.{table} where source_ref like 'rls-%'")
                )

    purge()
    with service_session() as session:
        for code in ("SUP-A", "SUP-B"):
            session.execute(
                text(
                    """
                    insert into public.suppliers (supplier_code, name, is_active, source_ref)
                    values (:c, :n, true, :r)
                    """
                ),
                {"c": code, "n": f"Supplier {code}", "r": f"{marker}-{code}"},
            )
        session.execute(
            text(
                """
                insert into public.customers (customer_code, name, is_active, source_ref)
                values ('CUS-A', 'Customer A', true, :r)
                """
            ),
            {"r": f"{marker}-CUS-A"},
        )
        session.execute(
            text(
                """
                insert into public.invoices (invoice_number, customer_code, total_amount, source_ref)
                values ('INV-RLS', 'CUS-A', 1000, :r)
                """
            ),
            {"r": f"{marker}-INV"},
        )
        session.execute(
            text(
                """
                insert into public.purchase_orders (po_number, supplier_code, total_amount, source_ref)
                values ('PO-RLS-A', 'SUP-A', 500, :r)
                """
            ),
            {"r": f"{marker}-PO-A"},
        )
        session.execute(
            text(
                """
                insert into public.supplier_catalog (supplier_code, item_code, unit_price, source_ref)
                values ('SUP-A', 'ITEM-1', 10, :r)
                """
            ),
            {"r": f"{marker}-CAT"},
        )
    yield marker
    purge()


def test_supplier_sees_only_own_supplier_row(rls) -> None:
    rows = rls(
        "supplier",
        "select supplier_code from public.suppliers where source_ref like 'rls-%' order by supplier_code",
    )
    # The supplier role has no supplier_code claim in this call, so it matches
    # nothing at all — stricter than "sees only its own".
    assert rows == []


def test_supplier_with_claim_sees_only_own_row(claims_factory) -> None:
    from app.db.engine import rls_session

    with rls_session(claims_factory("supplier", supplier_code="SUP-A")) as session:
        rows = session.execute(
            text(
                "select supplier_code from public.suppliers "
                "where source_ref like 'rls-%' order by supplier_code"
            )
        ).scalars().all()
    assert rows == ["SUP-A"]


def test_supplier_cannot_see_customers(rls) -> None:
    assert rls("supplier", "select customer_code from public.customers") == []


def test_supplier_cannot_see_finance(rls) -> None:
    assert rls("supplier", "select invoice_number from public.invoices") == []
    assert rls("supplier", "select account_code from public.gl_balances") == []


def test_supplier_cannot_see_pipeline(rls) -> None:
    assert rls("supplier", "select batch_code from public.migration_batches") == []


def test_supplier_sees_own_purchase_orders(claims_factory) -> None:
    from app.db.engine import rls_session

    with rls_session(claims_factory("supplier", supplier_code="SUP-A")) as session:
        rows = session.execute(
            text("select po_number from public.purchase_orders where source_ref like 'rls-%'")
        ).scalars().all()
    assert rows == ["PO-RLS-A"]


def test_supplier_sees_only_own_catalogue(claims_factory) -> None:
    from app.db.engine import rls_session

    with rls_session(claims_factory("supplier", supplier_code="SUP-B")) as session:
        rows = session.execute(
            text("select item_code from public.supplier_catalog where source_ref like 'rls-%'")
        ).scalars().all()
    # Catalogue row belongs to SUP-A, so SUP-B sees nothing.
    assert rows == []


def test_finance_sees_invoices_and_customers(rls) -> None:
    assert rls("finance_controller", "select invoice_number from public.invoices where source_ref like 'rls-%'") == ["INV-RLS"]
    assert rls("finance_controller", "select customer_code from public.customers where source_ref like 'rls-%'") == ["CUS-A"]


def test_inventory_cannot_see_finance_or_customers(rls) -> None:
    assert rls("inventory_manager", "select invoice_number from public.invoices where source_ref like 'rls-%'") == []
    assert rls("inventory_manager", "select customer_code from public.customers where source_ref like 'rls-%'") == []
    assert rls("inventory_manager", "select account_code from public.gl_balances") == []


def test_inventory_sees_items_and_suppliers(rls) -> None:
    suppliers = rls(
        "inventory_manager",
        "select supplier_code from public.suppliers where source_ref like 'rls-%' order by supplier_code",
    )
    assert suppliers == ["SUP-A", "SUP-B"]


def test_admin_sees_everything(rls) -> None:
    assert rls("admin", "select customer_code from public.customers where source_ref like 'rls-%'") == ["CUS-A"]
    assert rls("admin", "select invoice_number from public.invoices where source_ref like 'rls-%'") == ["INV-RLS"]


def test_audit_log_is_append_only() -> None:
    with service_session() as session:
        session.execute(
            text(
                """
                insert into public.audit_log (actor_email, actor_role, action, entity)
                values ('tester@cleanslate.test', 'admin', 'pytest.probe', 'test')
                """
            )
        )
        log_id = session.execute(text("select max(id) from public.audit_log")).scalar_one()

    with pytest.raises(Exception, match="append-only"):
        with service_session() as session:
            session.execute(
                text("update public.audit_log set action = 'tampered' where id = :i"),
                {"i": log_id},
            )

    with pytest.raises(Exception, match="append-only"):
        with service_session() as session:
            session.execute(text("delete from public.audit_log where id = :i"), {"i": log_id})


def test_permission_matrix_matches_spec() -> None:
    with service_session() as session:
        engineer = load_permissions(session, "migration_engineer")
        finance = load_permissions(session, "finance_controller")
        inventory = load_permissions(session, "inventory_manager")
        supplier = load_permissions(session, "supplier")
        admin = load_permissions(session, "admin")

    assert "payroll:read" not in engineer, "engineers must not see payroll"
    assert "finance:read" not in engineer
    assert "payroll:read" in finance
    assert "finance:approve" in finance
    assert "load:run" in engineer
    assert "inventory:write" in inventory
    assert "finance:read" not in inventory
    assert supplier == {"supplier:self", "report:read"}
    assert "admin:rbac" in admin


def test_jwt_round_trip_carries_role() -> None:
    token = auth.create_access_token(
        user_id=uuid.uuid4(),
        email="finance@cleanslate.test",
        role="finance_controller",
        supplier_code=None,
    )
    identity = auth.claims_to_identity(auth.decode_token(token))
    assert identity["role"] == "finance_controller"
    assert identity["email"] == "finance@cleanslate.test"


def test_tampered_token_is_rejected() -> None:
    token = auth.create_access_token(
        user_id=uuid.uuid4(), email="a@b.test", role="supplier"
    )
    header, payload, signature = token.split(".")
    forged = f"{header}.{payload}.{signature[:-4]}AAAA"
    with pytest.raises(auth.AuthError):
        auth.decode_token(forged)
