"""API tests: authentication, RBAC enforcement and the endpoint contract.

These exercise the real app against real Postgres and Mongo, because the things
worth testing here are the things the database enforces: who may call what, what
the responses contain, and that a sensitive value never appears in one.
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.engine import service_session
from app.main import app
from app.security import auth

pytestmark = pytest.mark.api

PASSWORD = "cleanslate-test-password"

ACCOUNTS = {
    "admin": "pytest.api.admin@cleanslate.test",
    "migration_engineer": "pytest.api.engineer@cleanslate.test",
    "finance_controller": "pytest.api.finance@cleanslate.test",
    "inventory_manager": "pytest.api.inventory@cleanslate.test",
    "supplier": "pytest.api.supplier@cleanslate.test",
}


@pytest.fixture(scope="module", autouse=True)
def _accounts():
    password_hash = auth.hash_password(PASSWORD)
    with service_session() as session:
        for role, email in ACCOUNTS.items():
            session.execute(
                text(
                    """
                    insert into public.app_users (email, full_name, password_hash, role)
                    values (:email, :name, :pw, :role)
                    on conflict (email) do update
                      set password_hash = excluded.password_hash,
                          role = excluded.role,
                          is_active = true
                    """
                ),
                {"email": email, "name": f"Pytest {role}", "pw": password_hash, "role": role},
            )

        # Link the external account to a supplier that actually has purchase
        # orders. The RLS policy matches on supplier_code, so an unlinked
        # account is correctly shown nothing, which would make the isolation
        # test vacuous rather than proving isolation.
        supplier = session.execute(
            text(
                """
                select s.id
                  from public.suppliers s
                  left join public.purchase_orders po on po.supplier_code = s.supplier_code
                 group by s.id
                 order by count(po.id) desc, s.supplier_code
                 limit 1
                """
            )
        ).scalar()
        if supplier is not None:
            session.execute(
                text(
                    "update public.app_users set supplier_id = :sid where email = :email"
                ),
                {"sid": supplier, "email": ACCOUNTS["supplier"]},
            )
    yield
    with service_session() as session:
        session.execute(
            text("delete from public.app_users where email = any(:emails)"),
            {"emails": list(ACCOUNTS.values())},
        )


@pytest.fixture(scope="module")
def client() -> TestClient:
    # raise_server_exceptions=False so a 500 surfaces as a response the test can
    # assert on, rather than an exception that hides which endpoint failed.
    return TestClient(app, raise_server_exceptions=False)


def _headers(client: TestClient, role: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login", json={"email": ACCOUNTS[role], "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture(scope="module")
def engineer(client: TestClient) -> dict[str, str]:
    return _headers(client, "migration_engineer")


# ------------------------------------------------------------------ health --


def test_health_reports_each_dependency(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["postgres"]["postgres"] == "ok"
    assert body["mongo"]["mongo"] == "ok"
    assert body["ok"] is True


def test_openapi_is_served(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    assert "/api/v1/batches" in schema["paths"]
    assert "/api/v1/review/{review_id}/approve" in schema["paths"]


# -------------------------------------------------------------------- auth --


def test_login_returns_a_token_and_the_user(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": ACCOUNTS["migration_engineer"], "password": PASSWORD},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["role"] == "migration_engineer"
    assert "password" not in str(body).lower()


def test_wrong_password_is_refused(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"email": ACCOUNTS["migration_engineer"], "password": "nope"}
    )
    assert response.status_code == 401


def test_unknown_account_gives_the_same_error_as_a_wrong_password(client: TestClient) -> None:
    """The endpoint must not reveal which accounts exist."""
    unknown = client.post(
        "/api/v1/auth/login", json={"email": "nobody@cleanslate.test", "password": "nope"}
    )
    wrong = client.post(
        "/api/v1/auth/login", json={"email": ACCOUNTS["migration_engineer"], "password": "nope"}
    )
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["detail"] == wrong.json()["detail"]


def test_a_malformed_email_is_rejected_before_any_lookup(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"email": "not-an-email", "password": "x"}
    )
    assert response.status_code == 422


def test_protected_routes_need_a_token(client: TestClient) -> None:
    for path in ("/api/v1/batches", "/api/v1/findings", "/api/v1/audit", "/api/v1/review"):
        assert client.get(path).status_code == 401, path


def test_a_garbage_token_is_refused(client: TestClient) -> None:
    response = client.get("/api/v1/batches", headers={"Authorization": "Bearer not.a.token"})
    assert response.status_code == 401


def test_a_non_bearer_header_is_refused(client: TestClient) -> None:
    response = client.get("/api/v1/batches", headers={"Authorization": "Basic abc"})
    assert response.status_code == 401


def test_me_reports_permissions_and_capabilities(client: TestClient, engineer) -> None:
    body = client.get("/api/v1/auth/me", headers=engineer).json()
    assert body["user"]["role"] == "migration_engineer"
    assert "ingest:run" in body["permissions"]
    assert body["capabilities"]["can_ingest"] is True
    # A migration engineer runs the pipeline but must not see payroll.
    assert body["capabilities"]["can_view_payroll"] is False


# -------------------------------------------------------------------- RBAC --


def test_supplier_cannot_read_finance_or_audit(client: TestClient) -> None:
    headers = _headers(client, "supplier")
    assert client.get("/api/v1/audit", headers=headers).status_code == 403
    assert client.get("/api/v1/reports/00000000-0000-4000-8000-000000000001", headers=headers).status_code in (403, 404)


def test_supplier_cannot_create_a_batch(client: TestClient) -> None:
    headers = _headers(client, "supplier")
    response = client.post("/api/v1/batches", json={"name": "nope"}, headers=headers)
    assert response.status_code == 403


def test_supplier_cannot_decide_review_items(client: TestClient) -> None:
    headers = _headers(client, "supplier")
    response = client.post(
        "/api/v1/review/00000000-0000-4000-8000-000000000001/approve",
        json={"note": ""},
        headers=headers,
    )
    assert response.status_code == 403


def test_inventory_manager_cannot_run_the_load(client: TestClient) -> None:
    headers = _headers(client, "inventory_manager")
    response = client.post(
        "/api/v1/batches/00000000-0000-4000-8000-000000000001/load", headers=headers
    )
    assert response.status_code == 403


def test_finance_controller_cannot_ingest(client: TestClient) -> None:
    headers = _headers(client, "finance_controller")
    assert client.get("/api/v1/batches", headers=headers).status_code == 200
    response = client.post("/api/v1/batches", json={"name": "nope"}, headers=headers)
    assert response.status_code == 403


def test_only_admin_can_delete_a_batch(client: TestClient, engineer) -> None:
    """Delete is the one destructive batch action, so it is admin-only."""
    batch = client.post("/api/v1/batches", json={"name": "RBAC delete probe"}, headers=engineer)
    batch_id = batch.json()["id"]
    try:
        assert client.delete(f"/api/v1/batches/{batch_id}", headers=engineer).status_code == 403
        admin = _headers(client, "admin")
        assert client.delete(f"/api/v1/batches/{batch_id}", headers=admin).status_code == 204
    finally:
        with service_session() as session:
            session.execute(
                text("delete from public.migration_batches where id = cast(:b as uuid)"),
                {"b": batch_id},
            )


def test_the_403_body_names_the_missing_permission(client: TestClient) -> None:
    headers = _headers(client, "supplier")
    detail = client.post("/api/v1/batches", json={"name": "x"}, headers=headers).json()["detail"]
    assert detail["error"] == "insufficient_permissions"
    assert detail["role"] == "supplier"
    assert "batch:create" in detail["missing"]


# ----------------------------------------------------------------- batches --


def test_create_read_and_list_a_batch(client: TestClient, engineer) -> None:
    created = client.post(
        "/api/v1/batches",
        json={"name": "API test batch", "fiscal_year_from": 2023, "fiscal_year_to": 2026},
        headers=engineer,
    )
    assert created.status_code == 201
    batch = created.json()
    batch_id = batch["id"]
    try:
        assert batch["status"] == "created"
        assert batch["record_count_in"] == 0

        fetched = client.get(f"/api/v1/batches/{batch_id}", headers=engineer)
        assert fetched.status_code == 200
        # Every stage is pre-created in a pending state.
        assert len(fetched.json()["stages"]) == 9

        listing = client.get("/api/v1/batches?limit=5", headers=engineer)
        assert listing.status_code == 200
        assert any(row["id"] == batch_id for row in listing.json())
    finally:
        with service_session() as session:
            session.execute(
                text("delete from public.migration_batches where id = cast(:b as uuid)"),
                {"b": batch_id},
            )


def test_an_unknown_batch_is_a_404(client: TestClient, engineer) -> None:
    response = client.get(
        "/api/v1/batches/00000000-0000-4000-8000-00000000dead", headers=engineer
    )
    assert response.status_code == 404


def test_a_malformed_batch_id_is_a_422(client: TestClient, engineer) -> None:
    assert client.get("/api/v1/batches/not-a-uuid", headers=engineer).status_code == 422


def test_stages_refuse_to_run_before_anything_is_ingested(client: TestClient, engineer) -> None:
    batch_id = client.post(
        "/api/v1/batches", json={"name": "empty batch"}, headers=engineer
    ).json()["id"]
    try:
        for stage in ("profile", "validate", "clean"):
            response = client.post(f"/api/v1/batches/{batch_id}/{stage}", headers=engineer)
            assert response.status_code == 409, stage
        # The dry run needs cleaned data, so it is a conflict too.
        assert client.post(f"/api/v1/batches/{batch_id}/dry-run", headers=engineer).status_code == 409
    finally:
        with service_session() as session:
            session.execute(
                text("delete from public.migration_batches where id = cast(:b as uuid)"),
                {"b": batch_id},
            )


# ------------------------------------------------------------------ ingest --


def test_ingest_rejects_an_unknown_entity(client: TestClient, engineer) -> None:
    batch_id = client.post(
        "/api/v1/batches", json={"name": "ingest probe"}, headers=engineer
    ).json()["id"]
    try:
        response = client.post(
            f"/api/v1/batches/{batch_id}/ingest",
            data={"entity": "not_an_entity"},
            files={"file": ("x.csv", io.BytesIO(b"a,b\n1,2\n"), "text/csv")},
            headers=engineer,
        )
        assert response.status_code == 422
    finally:
        with service_session() as session:
            session.execute(
                text("delete from public.migration_batches where id = cast(:b as uuid)"),
                {"b": batch_id},
            )


def test_ingest_rejects_an_oversized_file(client: TestClient, engineer) -> None:
    """A file over the limit is refused before it is parsed."""
    from app.config import settings

    batch_id = client.post(
        "/api/v1/batches", json={"name": "big file probe"}, headers=engineer
    ).json()["id"]
    try:
        oversized = b"a" * (settings.max_upload_mb * 1024 * 1024 + 1)
        response = client.post(
            f"/api/v1/batches/{batch_id}/ingest",
            data={"entity": "suppliers"},
            files={"file": ("big.csv", io.BytesIO(oversized), "text/csv")},
            headers=engineer,
        )
        assert response.status_code == 422
    finally:
        with service_session() as session:
            session.execute(
                text("delete from public.migration_batches where id = cast(:b as uuid)"),
                {"b": batch_id},
            )


def test_ingest_stores_rows_and_reports_them(client: TestClient, engineer) -> None:
    batch_id = client.post(
        "/api/v1/batches", json={"name": "ingest ok"}, headers=engineer
    ).json()["id"]
    csv = (
        "record_id,supplier_code,name,gstin,pan,email,phone,pincode,city,state,"
        "country,payment_terms,currency,is_active,last_activity_date\n"
        "SUP-1,SUP-00001,Acme Supplies,27ABCDE1234F1Z5,ABCDE1234F,"
        "a@acme.test,9876543210,400001,Mumbai,Maharashtra,IN,NET30,INR,true,2025-01-01\n"
    )
    try:
        response = client.post(
            f"/api/v1/batches/{batch_id}/ingest",
            data={"entity": "suppliers"},
            files={"file": ("suppliers.csv", io.BytesIO(csv.encode()), "text/csv")},
            headers=engineer,
        )
        assert response.status_code == 200, response.text
        assert response.json()["summary"]["rows"] == 1

        detail = client.get(f"/api/v1/batches/{batch_id}", headers=engineer).json()
        assert [f["entity"] for f in detail["ingested"]] == ["suppliers"]
    finally:
        from app.db import mongo

        with service_session() as session:
            session.execute(
                text("delete from public.migration_batches where id = cast(:b as uuid)"),
                {"b": batch_id},
            )
        mongo.collection(mongo.RAW_RECORDS).delete_many({"batch_id": batch_id})


# --------------------------------------------------------------- findings ---


def test_findings_pagination_and_filters(client: TestClient, engineer) -> None:
    batch_id = client.post(
        "/api/v1/batches", json={"name": "findings probe"}, headers=engineer
    ).json()["id"]
    try:
        page = client.get(f"/api/v1/findings?batch_id={batch_id}&limit=10", headers=engineer)
        assert page.status_code == 200
        body = page.json()
        assert body["total"] == 0
        assert body["items"] == []

        summary = client.get(f"/api/v1/findings/summary?batch_id={batch_id}", headers=engineer)
        assert summary.status_code == 200
        assert summary.json()["total"] == 0
        assert "by_status" in summary.json()
    finally:
        with service_session() as session:
            session.execute(
                text("delete from public.migration_batches where id = cast(:b as uuid)"),
                {"b": batch_id},
            )


def test_findings_refuse_an_unknown_sort_column(client: TestClient, engineer) -> None:
    """The sort column is chosen from a fixed set, never interpolated blindly."""
    response = client.get("/api/v1/findings?sort=message;drop table audit_log", headers=engineer)
    assert response.status_code == 422


def test_rules_catalogue_lists_every_rule(client: TestClient, engineer) -> None:
    body = client.get("/api/v1/findings/rules", headers=engineer).json()
    assert len(body["rules"]) == 27
    assert {"rule_id", "severity", "auto_fixable", "params"} <= set(body["rules"][0])


# ------------------------------------------------------------------ audit ---


def test_audit_records_a_login(client: TestClient) -> None:
    admin = _headers(client, "admin")
    body = client.get("/api/v1/audit?limit=50", headers=admin).json()
    assert body["total"] > 0
    assert any(entry["action"] == "auth.login" for entry in body["items"])


def test_audit_entries_carry_who_and_when(client: TestClient) -> None:
    admin = _headers(client, "admin")
    entry = client.get("/api/v1/audit?limit=1", headers=admin).json()["items"][0]
    assert entry["actor_email"]
    assert entry["actor_role"]
    assert entry["occurred_at"]


def test_audit_log_cannot_be_modified(client: TestClient) -> None:
    """The demo claim is that the log is immutable. This proves it."""
    admin = _headers(client, "admin")
    body = client.get("/api/v1/audit/verify/immutability", headers=admin).json()
    assert body["immutable"] is True
    blocked = {item["operation"]: item["blocked"] for item in body["attempts"]}
    assert blocked == {"UPDATE": True, "DELETE": True}


# ------------------------------------------------------------ review, misc --


def test_review_queue_paginates(client: TestClient, engineer) -> None:
    body = client.get("/api/v1/review?status=pending&limit=5", headers=engineer).json()
    assert body["limit"] == 5
    assert isinstance(body["items"], list)


def test_an_unknown_review_item_is_a_404(client: TestClient, engineer) -> None:
    response = client.post(
        "/api/v1/review/00000000-0000-4000-8000-00000000dead/approve",
        json={"note": ""},
        headers=engineer,
    )
    assert response.status_code == 404


def test_inventory_insights_refuse_an_unknown_batch(client: TestClient) -> None:
    headers = _headers(client, "inventory_manager")
    response = client.get(
        "/api/v1/inventory/insights?batch_id=00000000-0000-4000-8000-00000000dead",
        headers=headers,
    )
    assert response.status_code == 404


def test_responses_carry_a_request_id(client: TestClient) -> None:
    response = client.get("/health")
    assert response.headers.get("x-request-id")


def test_a_supplied_request_id_is_echoed_back(client: TestClient) -> None:
    response = client.get("/health", headers={"x-request-id": "trace-me-123"})
    assert response.headers["x-request-id"] == "trace-me-123"


# ------------------------------------------------------------------ RLS -----


def test_rls_matrix_shows_the_matrix_enforced_by_postgres(client: TestClient) -> None:
    """The whole point: the database, not the API, decides what each role sees."""
    admin = _headers(client, "admin")
    body = client.get("/api/v1/security/rls-matrix", headers=admin).json()
    assert "postgres" in body["enforced_by"].lower()
    assert set(body["roles"]) == {
        "admin",
        "migration_engineer",
        "finance_controller",
        "inventory_manager",
        "supplier",
    }


def test_rls_hides_finance_from_the_migration_engineer(client: TestClient) -> None:
    admin = _headers(client, "admin")
    body = client.get("/api/v1/security/rls-matrix", headers=admin).json()
    engineer = {p["table"]: p for p in body["roles"]["migration_engineer"]}
    # The table has rows, but the policy returns none of them.
    assert engineer["invoices"]["total_rows"] > 0
    assert engineer["invoices"]["visible"] is False
    assert engineer["invoices"]["row_count"] == 0
    assert engineer["invoices"]["denied_rows"] == engineer["invoices"]["total_rows"]


def test_rls_lets_the_finance_controller_read_finance(client: TestClient) -> None:
    admin = _headers(client, "admin")
    body = client.get("/api/v1/security/rls-matrix", headers=admin).json()
    finance = {p["table"]: p for p in body["roles"]["finance_controller"]}
    assert finance["invoices"]["visible"] is True
    assert finance["invoices"]["row_count"] == finance["invoices"]["total_rows"]


def test_rls_denies_the_supplier_the_audit_log(client: TestClient) -> None:
    admin = _headers(client, "admin")
    body = client.get("/api/v1/security/rls-matrix", headers=admin).json()
    supplier = {p["table"]: p for p in body["roles"]["supplier"]}
    assert supplier["audit_log"]["visible"] is False
    assert supplier["invoices"]["visible"] is False


def test_the_supplier_sees_only_their_own_purchase_orders(client: TestClient) -> None:
    """The load-bearing security test: a supplier cannot see another's orders."""
    headers = _headers(client, "supplier")
    response = client.get("/api/v1/supplier/purchase-orders", headers=headers)
    assert response.status_code == 200
    orders = response.json()
    if not orders:
        pytest.skip("the supplier demo account has no purchase orders in this database")
    codes = {order["supplier_code"] for order in orders}
    assert len(codes) == 1, f"a supplier saw more than one supplier's orders: {codes}"

    portal = client.get("/api/v1/supplier/portal", headers=headers).json()
    assert portal["purchase_order_count"] == len(orders)
    assert all(order["supplier_code"] == portal["supplier_code"] for order in orders)


def test_the_supplier_portal_is_closed_to_internal_roles(client: TestClient, engineer) -> None:
    """An internal user reading the portal would see every supplier's orders."""
    assert client.get("/api/v1/supplier/portal", headers=engineer).status_code == 403


def test_the_supplier_cannot_reach_finance_surfaces(client: TestClient) -> None:
    headers = _headers(client, "supplier")
    assert client.get("/api/v1/audit", headers=headers).status_code == 403
    assert client.get("/api/v1/security/rls-matrix", headers=headers).status_code == 403
    assert client.get("/api/v1/findings", headers=headers).status_code == 403


def test_rls_matrix_needs_audit_access(client: TestClient) -> None:
    headers = _headers(client, "inventory_manager")
    assert client.get("/api/v1/security/rls-matrix", headers=headers).status_code == 403
