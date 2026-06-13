"""Application configuration loaded from environment variables."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    supabase_url: str = ""
    supabase_service_key: str = ""
    whatsapp_verify_token: str = ""
    log_level: str = "INFO"
    gdpr_retention_days: int = 30

    disclaimer: str = (
        "Dette er et automatisert beslutningsgrunnlag. "
        "Endelig kontroll og juridisk ansvarlig godkjenning ligger hos arbeidsgiver/murermester."
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
