"""Authentication: password hashing, JWT issue and verification.

Works in two modes and the API code does not care which is active:

* ``supabase`` — verifies tokens issued by Supabase Auth using the project's JWT
  secret, and reads the role from ``app_metadata``.
* ``local``    — the backend issues and verifies its own HS256 tokens, so the
  demo runs with no external dependency.

The verified claims are what the RBAC dependency and the RLS session both use,
so there is a single source of truth for "who is this and what may they do".
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from passlib.context import CryptContext

from app.config import settings

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class AuthError(Exception):
    """Raised when a token is missing, malformed, expired or badly signed."""


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        return _pwd_context.verify(password, password_hash)
    except ValueError:
        return False


def _secret() -> str:
    if settings.effective_auth_mode == "supabase":
        if not settings.supabase_jwt_secret:
            raise AuthError("SUPABASE_JWT_SECRET is not configured")
        return settings.supabase_jwt_secret
    return settings.local_jwt_secret


def create_access_token(
    *,
    user_id: str | uuid.UUID,
    email: str,
    role: str,
    full_name: str = "",
    supplier_id: str | uuid.UUID | None = None,
    supplier_code: str | None = None,
    expires_minutes: int | None = None,
) -> str:
    """Issue an HS256 access token carrying the role in app_metadata.

    app_metadata is used rather than a top-level claim because Supabase forbids
    users from writing to it, so a client cannot escalate its own role.
    """
    now = datetime.now(UTC)
    ttl = expires_minutes or settings.access_token_ttl_minutes
    app_metadata: dict[str, Any] = {"role": role}
    if supplier_id:
        app_metadata["supplier_id"] = str(supplier_id)
    if supplier_code:
        app_metadata["supplier_code"] = supplier_code

    payload: dict[str, Any] = {
        "sub": str(user_id),
        "email": email,
        "aud": "authenticated",
        "role": "authenticated",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=ttl)).timestamp()),
        "app_metadata": app_metadata,
        "user_metadata": {"full_name": full_name},
    }
    return jwt.encode(payload, _secret(), algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(
            token,
            _secret(),
            algorithms=[settings.jwt_algorithm],
            audience="authenticated",
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("Token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError(f"Invalid token: {exc}") from exc


def claims_to_identity(claims: dict[str, Any]) -> dict[str, Any]:
    """Flatten verified JWT claims into the shape the app uses everywhere."""
    app_metadata = claims.get("app_metadata") or {}
    role = app_metadata.get("role") or "anon"
    return {
        "user_id": claims.get("sub"),
        "email": claims.get("email") or "",
        "role": role,
        "full_name": (claims.get("user_metadata") or {}).get("full_name", ""),
        "supplier_id": app_metadata.get("supplier_id"),
        "supplier_code": app_metadata.get("supplier_code"),
    }


def rls_claims(claims: dict[str, Any]) -> dict[str, Any]:
    """The minimal claim set handed to Postgres for RLS evaluation."""
    return {
        "sub": claims.get("sub"),
        "role": claims.get("role", "authenticated"),
        "email": claims.get("email"),
        "app_metadata": claims.get("app_metadata") or {},
    }
