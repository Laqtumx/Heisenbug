"""Application settings, loaded from environment / .env."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------------------------------------------ app --
    app_name: str = "CleanSlate"
    environment: Literal["local", "dev", "staging", "production"] = "local"
    debug: bool = True
    api_prefix: str = "/api/v1"
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # ------------------------------------------------------------ postgres --
    # On Supabase, set this to the project's connection string (use the pooler
    # for serverless deployments). Locally it points at the docker container.
    database_url: str = "postgresql+psycopg://cleanslate:cleanslate@127.0.0.1:5433/cleanslate"

    # The RLS-enforced path connects as a non-superuser role, because Postgres
    # superusers always bypass Row Level Security. Set rls_database_url to point
    # at a different database entirely; locally the user/password below are
    # substituted into database_url. On Supabase leave both blank.
    rls_database_url: str = ""
    rls_db_user: str = "cleanslate_authenticated"
    rls_db_password: str = "cleanslate_authenticated"

    # --------------------------------------------------------------- mongo --
    mongo_url: str = "mongodb://127.0.0.1:27018"
    mongo_db: str = "cleanslate"

    # ------------------------------------------------------------ supabase --
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    supabase_jwt_secret: str = ""

    # Auth can run fully self-contained when Supabase is not configured. The
    # backend then signs its own HS256 tokens with this secret.
    auth_mode: Literal["auto", "supabase", "local"] = "auto"
    local_jwt_secret: str = "dev-only-change-me-6f2b1c9a4e7d8f30b5a2c6e1d9f4a7b3"
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 480

    # --------------------------------------------------------------- crypto --
    # Fernet key for column-level encryption. Generate with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    field_encryption_key: str = ""
    blind_index_key: str = "dev-only-blind-index-key"

    # ------------------------------------------------------------------ llm --
    grok_api_key: str = ""
    grok_base_url: str = "https://api.x.ai/v1"
    grok_model: str = "grok-2-latest"

    nemotron_api_key: str = ""
    nemotron_base_url: str = "https://integrate.api.nvidia.com/v1"
    nemotron_model: str = "nvidia/llama-3.3-nemotron-super-49b-v1"

    llm_provider: Literal["auto", "grok", "nemotron", "fallback"] = "auto"
    llm_timeout_seconds: float = 20.0
    llm_max_retries: int = 2
    llm_cache_ttl_seconds: int = 3600
    llm_enabled: bool = True

    # ------------------------------------------------------------ pipeline --
    max_upload_mb: int = 100
    allowed_upload_extensions: str = ".csv,.xlsx,.xls,.json,.sql,.tsv"
    upload_dir: str = "./var/uploads"

    # Stale master data older than this many years is archived, not migrated.
    archive_after_years: int = 3
    # The date staleness, future-dating and ageing checks are measured against.
    # Fixed rather than "today" so a validation run is reproducible: a batch
    # validated today and re-validated next month must produce the same findings.
    reference_date: str = "2026-01-01"
    # Fuzzy duplicate detection threshold (0-100).
    fuzzy_duplicate_threshold: int = 88
    # Rows above this are processed in vectorised chunks.
    chunk_size: int = 100_000

    rate_limit_default: str = "300/minute"
    rate_limit_upload: str = "20/minute"

    @field_validator("cors_origins")
    @classmethod
    def _strip_origins(cls, v: str) -> str:
        return v.strip()

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def allowed_extensions(self) -> list[str]:
        return [e.strip().lower() for e in self.allowed_upload_extensions.split(",") if e.strip()]

    @property
    def effective_auth_mode(self) -> str:
        if self.auth_mode != "auto":
            return self.auth_mode
        if self.supabase_url and self.supabase_jwt_secret:
            return "supabase"
        return "local"

    @property
    def sqlalchemy_url(self) -> str:
        url = self.database_url
        # Normalise bare postgres:// URLs handed out by some providers.
        if url.startswith("postgres://"):
            url = "postgresql+psycopg://" + url[len("postgres://") :]
        elif url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://") :]
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
