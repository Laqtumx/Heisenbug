"""Column-level encryption and blind indexes.

Two separate primitives:

* ``encrypt`` / ``decrypt`` — Fernet (AES-128-CBC + HMAC-SHA256) for sensitive
  columns such as tax identifiers and salary figures.
* ``blind_index`` — HMAC-SHA256 of a normalised value, stored alongside the
  ciphertext. Duplicate detection and lookup work on the index, so the platform
  never has to decrypt a whole column to find a match.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

logger = logging.getLogger(__name__)

_NORMALISE_RE = re.compile(r"[^a-z0-9]")


@lru_cache
def _fernet() -> Fernet:
    key = settings.field_encryption_key.strip()
    if not key:
        # Derive a stable development key so a fresh clone runs without setup.
        # Production must set FIELD_ENCRYPTION_KEY explicitly.
        if settings.environment in ("staging", "production"):
            raise RuntimeError(
                "FIELD_ENCRYPTION_KEY must be set outside local development"
            )
        digest = hashlib.sha256(settings.local_jwt_secret.encode()).digest()
        key = base64.urlsafe_b64encode(digest).decode()
        logger.warning(
            "FIELD_ENCRYPTION_KEY is unset; using a derived development key. "
            "Set it explicitly for any shared environment."
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def generate_key() -> str:
    """Return a fresh Fernet key for .env."""
    return Fernet.generate_key().decode()


def normalise_for_index(value: str) -> str:
    return _NORMALISE_RE.sub("", value.strip().lower())


def blind_index(value: str | None) -> str | None:
    """Deterministic, keyed lookup token. Same input always gives same output."""
    if value is None:
        return None
    normalised = normalise_for_index(str(value))
    if not normalised:
        return None
    return hmac.new(
        settings.blind_index_key.encode(),
        normalised.encode(),
        hashlib.sha256,
    ).hexdigest()


def encrypt(value: str | None) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    return _fernet().encrypt(str(value).encode()).decode()


def decrypt(token: str | None) -> str | None:
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        # A value that cannot be decrypted is treated as absent rather than
        # crashing a report. The row still exists and is still traceable.
        logger.warning("Failed to decrypt a field; returning None")
        return None


MASK = "••••••"


def mask(value: str | None, *, keep_last: int = 4, visible: bool = False) -> str | None:
    """Mask a sensitive value for display. ``visible`` bypasses masking."""
    if value is None:
        return None
    if visible:
        return value
    text = str(value)
    if len(text) <= keep_last:
        return MASK
    return MASK + text[-keep_last:]


def mask_email(value: str | None, *, visible: bool = False) -> str | None:
    if value is None:
        return None
    if visible:
        return value
    local, _, domain = str(value).partition("@")
    if not domain:
        return MASK
    head = local[:1] if local else ""
    return f"{head}{MASK}@{domain}"


def mask_phone(value: str | None, *, visible: bool = False) -> str | None:
    if value is None:
        return None
    if visible:
        return value
    text = str(value)
    return MASK + text[-2:] if len(text) > 2 else MASK


def encryption_ready() -> bool:
    """Whether an explicit key is configured.

    False does not mean encryption is off: a development key is derived so a
    fresh clone runs. It means the key is not the operator's own, which matters
    before real data is stored.
    """
    return bool(settings.field_encryption_key.strip())
