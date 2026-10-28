"""FastAPI dependencies for authentication, RBAC and the audit trail.

Route handlers declare what they need, for example::

    @router.post("/load")
    def load(batch_id: uuid.UUID, user: CurrentUser = Depends(require("load:run"))): ...

The permission set is read from the database, not from a hardcoded dict, so the
matrix in 002_rbac.sql is the only place permissions are defined.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.engine import rls_session, service_session
from app.security import auth

SENSITIVE_ROLES = {"admin", "migration_engineer", "finance_controller", "inventory_manager", "supplier"}


@dataclass(frozen=True)
class CurrentUser:
    user_id: str
    email: str
    role: str
    full_name: str = ""
    supplier_id: str | None = None
    supplier_code: str | None = None
    permissions: frozenset[str] = field(default_factory=frozenset)

    @property
    def is_external(self) -> bool:
        return self.role == "supplier"

    def can(self, permission: str) -> bool:
        return permission in self.permissions

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def _extract_bearer(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header must be 'Bearer <token>'",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token


def load_permissions(session: Session, role: str) -> frozenset[str]:
    rows = session.execute(
        text("select permission from public.role_permissions where role = :role"),
        {"role": role},
    ).scalars()
    return frozenset(rows)


def get_claims(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    token = _extract_bearer(authorization)
    try:
        return auth.decode_token(token)
    except auth.AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def get_current_user(claims: dict[str, Any] = Depends(get_claims)) -> CurrentUser:
    identity = auth.claims_to_identity(claims)
    role = identity["role"]
    if role not in SENSITIVE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Unknown or missing role: {role!r}",
        )
    with service_session() as session:
        permissions = load_permissions(session, role)
    return CurrentUser(permissions=permissions, **identity)


def require(*permissions: str) -> Callable[[CurrentUser], CurrentUser]:
    """Dependency factory. Caller must hold every listed permission."""

    def _dependency(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        missing = [p for p in permissions if p not in user.permissions]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "insufficient_permissions",
                    "role": user.role,
                    "required": list(permissions),
                    "missing": missing,
                },
            )
        return user

    return _dependency


def require_roles(*roles: str) -> Callable[[CurrentUser], CurrentUser]:
    def _dependency(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": "role_not_permitted", "role": user.role, "allowed": list(roles)},
            )
        return user

    return _dependency


def rls_db(claims: dict[str, Any] = Depends(get_claims)) -> Iterator[Session]:
    """A session where Postgres enforces RLS for this caller."""
    with rls_session(auth.rls_claims(claims)) as session:
        yield session


def db() -> Iterator[Session]:
    """Trusted session for pipeline work that must bypass RLS."""
    with service_session() as session:
        yield session


def request_context(request: Request) -> dict[str, Any]:
    return {
        "request_id": request.headers.get("x-request-id") or uuid.uuid4().hex,
        "ip_address": request.client.host if request.client else None,
        "path": request.url.path,
        "method": request.method,
    }


__all__ = [
    "CurrentUser",
    "db",
    "get_claims",
    "get_current_user",
    "require",
    "require_roles",
    "request_context",
    "rls_db",
]
