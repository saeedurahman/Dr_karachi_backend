from __future__ import annotations

from decimal import Decimal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ────────────────────────────────────────────────────
    APP_NAME: str = "Karachi Lab, Pharmacy & Clinics API"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    ENVIRONMENT: str = "development"

    # ── Database ───────────────────────────────────────────────
    DATABASE_URL: str  # async URL  (asyncpg)
    SYNC_DATABASE_URL: str  # sync URL   (psycopg2, Alembic)

    # ── JWT ────────────────────────────────────────────────────
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # ── File Storage ───────────────────────────────────────────
    STORAGE_BACKEND: str = "s3"  # "local" | "s3"

    # S3 / Cloudflare R2
    S3_ENDPOINT_URL: str = ""
    S3_ACCESS_KEY_ID: str = ""
    S3_SECRET_ACCESS_KEY: str = ""
    S3_BUCKET_NAME: str = "karachi-clinic-uploads"
    S3_PUBLIC_URL: str = ""  # CDN prefix

    # Local fallback
    LOCAL_UPLOAD_DIR: str = "./uploads"

    # ── Business Rules ─────────────────────────────────────────
    PLATFORM_FEE_RATE: Decimal = Decimal("0.02")
    DELIVERY_FEE: Decimal = Decimal(150)
    APPOINTMENT_SLOT_DURATION_MINUTES: int = 30

    # ── CORS ───────────────────────────────────────────────────
    ALLOWED_ORIGINS: str = "http://localhost:3000"

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    # ── Rate Limiting ──────────────────────────────────────────
    RATE_LIMIT_PER_MINUTE: int = 100


settings = Settings()  # type: ignore[call-arg]
