"""Authentication and identity endpoints."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import row_to_dict
from app.schemas.api import LoginRequest, MeResponse, TokenResponse, UserOut
from app.security import audit, auth
from app.security.deps import CurrentUser, db, get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])


def _capabilities(user: CurrentUser) -> dict[str, bool]:
    """What the UI may show this caller.

    Derived from the permission set the database holds, not from a hardcoded
    role list, so the navigation cannot drift from what the API actually
    permits. The server still enforces every rule; this only stops the UI from
    offering an action that would fail.
    """
    return {
        "can_manage_batches": user.can("batch:create"),
        "can_ingest": user.can("ingest:run"),
        "can_validate": user.can("validate:run"),
        "can_clean": user.can("clean:run"),
        "can_review": user.can("review:decide"),
        "can_map": user.can("map:run"),
        "can_load": user.can("load:run"),
        "can_rollback": user.can("rollback:run"),
        "can_view_finance": user.can("finance:read"),
        "can_approve_finance": user.can("finance:approve"),
        "can_view_payroll": user.can("payroll:read"),
        "can_view_inventory": user.can("inventory:read"),
        "can_view_audit": user.can("audit:read"),
        "can_view_own_catalog": user.can("catalog:read_own"),
        "is_external": user.is_external,
    }


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, session: Session = Depends(db)) -> TokenResponse:
    """Exchange an email and password for an access token.

    The same error is returned whether the email is unknown or the password is
    wrong, so the endpoint cannot be used to discover which accounts exist.
    """
    row = session.execute(
        text("select * from public.app_users where lower(email) = lower(:email)"),
        {"email": payload.email},
    ).mappings().first()

    if row is None or not auth.verify_password(payload.password, row["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )
    if not row["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="This account is disabled"
        )

    token = auth.create_access_token(
        user_id=row["id"],
        email=row["email"],
        role=row["role"],
        full_name=row["full_name"],
        supplier_id=row["supplier_id"],
    )
    session.execute(
        text("update public.app_users set last_login_at = now() where id = :id"),
        {"id": row["id"]},
    )
    audit.record(
        session,
        action="auth.login",
        user=row["email"],
        entity="app_users",
        entity_id=str(row["id"]),
        after={"role": row["role"]},
    )

    from app.config import settings

    return TokenResponse(
        access_token=token,
        expires_in=settings.access_token_ttl_minutes * 60,
        user=UserOut(**row_to_dict(row)),
    )


@router.get("/me", response_model=MeResponse)
def me(user: CurrentUser = Depends(get_current_user)) -> MeResponse:
    return MeResponse(
        user=UserOut(
            id=user.user_id,
            email=user.email,
            full_name=user.full_name,
            role=user.role,
            supplier_id=user.supplier_id,
        ),
        permissions=sorted(user.permissions),
        capabilities=_capabilities(user),
    )


@router.post("/logout")
def logout(user: CurrentUser = Depends(get_current_user), session: Session = Depends(db)) -> dict:
    """Record the sign-out.

    Tokens are stateless, so this does not invalidate the token itself; it marks
    the event in the audit trail. Revocation would need a deny-list, which is a
    deliberate omission for a token lifetime measured in hours.
    """
    audit.record(
        session,
        action="auth.logout",
        user=user,
        entity="app_users",
        entity_id=user.user_id,
        after={"at": datetime.now(UTC).isoformat()},
    )
    return {"status": "ok"}
