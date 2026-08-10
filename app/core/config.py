"""Application configuration loaded from environment variables / .env file."""

from decimal import Decimal
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central application settings, overridable via environment or a .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application -------------------------------------------------------
    APP_NAME: str = "AI Customer Care Bot"
    APP_VERSION: str = "0.1.0"
    API_V1_PREFIX: str = "/api/v1"
    ENVIRONMENT: str = "development"
    DEBUG: bool = False

    # --- Database ----------------------------------------------------------
    DATABASE_URL: str = (
        "postgresql+psycopg://customer_care:customer_care@localhost:5432/customer_care"
    )

    # --- JWT ---------------------------------------------------------------
    JWT_SECRET_KEY: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # --- OTP ---------------------------------------------------------------
    OTP_EXPIRE_MINUTES: int = 10
    # Failed attempts before the OTP is invalidated (brute-force protection).
    MAX_OTP_ATTEMPTS: int = 5
    # Dev convenience: expose the generated OTP in the register response.
    # MUST be disabled in production. Real deployments send the OTP via email/SMS.
    EXPOSE_OTP_IN_RESPONSE: bool = True

    # --- Knowledge base ----------------------------------------------------
    # Search backend: "database" (SQL). Future: "pinecone", "chromadb".
    KNOWLEDGE_SEARCH_BACKEND: str = "database"

    # --- Chat --------------------------------------------------------------
    # Bot reply backend: "stub" (deterministic offline placeholder) or
    # "groq" (the Groq-powered AI Service — the only LLM provider).
    CHAT_REPLY_BACKEND: str = "stub"

    # --- AI / Groq ---------------------------------------------------------
    # GROQ is the only supported LLM provider. The API key is read from the
    # environment (never hardcoded); without it, Groq calls degrade to a
    # graceful "assistant unavailable" fallback instead of crashing.
    GROQ_API_KEY: str = ""
    # Model name comes from the environment so it can be changed without code.
    GROQ_MODEL: str = "llama-3.3-70b-versatile"
    GROQ_TIMEOUT_SECONDS: float = 30.0
    # Number of recent chat messages sent to Groq per turn (context window).
    AI_MAX_CONTEXT_MESSAGES: int = 20

    # --- Orders ------------------------------------------------------------
    # Flat shipping cost and sales-tax rate applied at checkout.
    ORDER_SHIPPING_COST: Decimal = Decimal("4.99")
    ORDER_TAX_RATE: Decimal = Decimal("0.08")

    # --- Notifications -----------------------------------------------------
    # Delivery channel: "noop" (database only) until email / SMS / WhatsApp /
    # push providers are implemented as NotificationChannel backends.
    NOTIFICATION_CHANNEL: str = "noop"

    # --- CORS / misc -------------------------------------------------------
    CORS_ORIGINS: list[str] = ["http://localhost:3000"]
    LOG_LEVEL: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (recreated on cache clear)."""
    return Settings()
