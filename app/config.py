import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    """
    Application settings loaded from environment variables.

    All values can be overridden via environment or .env.example.

    LLM_PROVIDER:
        Set to "gemini" to enable Gemini API calls.
        Any other value (e.g. "mock") disables LLM and uses the deterministic fallback.

    GEMINI_API_KEY:
        Required when LLM_PROVIDER=gemini. Leave blank to use fallback only.

    GEMINI_MODEL:
        Defaults to gemini-1.5-flash. Can be changed to any supported Gemini model.

    UPLOAD_DIR:
        Directory where uploaded CSV files are stored inside the container.
    """

    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg2://postgres:postgres@db:5432/transactions_db",
    )
    celery_broker_url: str = os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0")
    celery_result_backend: str = os.getenv("CELERY_RESULT_BACKEND", "redis://redis:6379/1")
    upload_dir: str = os.getenv("UPLOAD_DIR", "/app/uploads")
    llm_provider: str = os.getenv("LLM_PROVIDER", "mock").lower()
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")


settings = Settings()
