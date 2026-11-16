"""Application settings (pydantic-settings). Secrets come from the environment / Secret Manager."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def to_async_url(url: str) -> str:
    """Accept plain ``postgresql://`` URLs and use the asyncpg driver."""
    for prefix in (
        "postgresql+psycopg://",
        "postgresql+psycopg2://",
        "postgres://",
        "postgresql://",
    ):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix) :]
    return url

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: Literal["dev", "test", "staging", "prod"] = "dev"
    log_level: str = "INFO"
    log_json: bool = False

    # --- database ---
    database_url: str = "postgresql+asyncpg://localhost/LAS_Email"
    # Tests only: a role that may CREATE DATABASE (tests use a throwaway las_test_<random>).
    test_database_admin_url: str | None = None
    db_pool_size: int = 10
    db_echo: bool = False

    # --- queue ---
    redis_url: str = "redis://localhost:6379/0"

    # --- mailbox (Zoho IMAP) ---
    las_mailbox_address: str = "las@integramicro.co.in"
    imap_host: str = "imap.zoho.in"
    imap_port: int = 993
    imap_username: str = "las@integramicro.co.in"
    imap_password: SecretStr = SecretStr("")
    imap_use_ssl: bool = True
    imap_inbox: str = "INBOX"
    imap_folder_processed: str = "LAS/Processed"
    imap_folder_rejected: str = "LAS/Rejected"
    imap_folder_review: str = "LAS/Review"
    imap_idle_renew_seconds: int = 25 * 60
    imap_catchup_seconds: int = 5 * 60

    # Our own sending address: mail from it is never treated as a BD's (auto-reply guard).
    mail_from_address: str = "las@integramicro.co.in"

    # --- sender verification ---
    trusted_authserv_ids: list[str] = Field(
        default_factory=lambda: ["mx.zohomail.com", "mx.zoho.in"]
    )
    auth_dns_fallback: bool = True

    # --- object storage ---
    local_store_path: str = (
        "var/objects"  # e.g. /srv/las/objects on the server (shared by all processes)
    )
    local_store_key_b64: SecretStr = SecretStr("")  # 32-byte AES key, base64; required outside dev
    public_base_url: str = "http://localhost:8000"
    url_signing_key: SecretStr = SecretStr("dev-url-signing-key-change-me")

    # --- attachments ---
    attachment_max_bytes: int = 20 * 1024 * 1024
    attachment_max_depth: int = 3
    attachment_max_count: int = 25
    zip_max_total_bytes: int = 60 * 1024 * 1024
    clamd_host: str = "localhost"
    clamd_port: int = 3310
    av_scanner: Literal["clamd", "fake"] = "clamd"

    # --- LLM (Gemini) ---
    gemini_api_key: SecretStr = SecretStr("")
    gemini_use_vertex: bool = False
    gemini_vertex_project: str = ""
    gemini_vertex_location: str = "asia-south1"
    llm_provider_name: str = "google_gemini"
    llm_extraction_model: str = "gemini-3.8-flash"
    llm_escalation_model: str = "gemini-3.8-flash"
    llm_vision_model: str = "gemini-3.8-flash"
    llm_dpr_model: str = "gemini-3.8-flash"
    llm_timeout_seconds: float = 60.0
    llm_max_api_retries: int = 2
    llm_confidence_threshold: float = 0.80
    llm_escalation_action_count: int = 3

    # --- document parsing (LiteParse: text layer + bundled Tesseract OCR, all local) ---
    document_parser: Literal["liteparse", "fake"] = "liteparse"
    ocr_language: str = "eng+hin+kan"  # Tesseract language codes
    parser_max_pages: int = 80
    # Folder with <lang>.traineddata; empty = LiteParse downloads to ~/.tesseract-rs/tessdata on first use.
    tessdata_path: str = ""

    # --- Core LAS (every processed mail is POSTed there; 2026-11-16) ---
    # Full URL, e.g. https://core.example/las-api/internal/v1/intake/events
    core_intake_url: str = ""
    core_intake_api_key: SecretStr = SecretStr("")
    core_intake_verify_ssl: bool = True
    core_intake_timeout_seconds: float = 30.0
    core_intake_batch_size: int = Field(default=20, ge=1, le=200)
    # Attachment bytes are sent only for files Core LAS can store (its document limits);
    # anything else goes as metadata so Core can tell the BD. The total cap keeps one event under
    # Core's request limit (base64 adds a third).
    core_intake_attachment_max_bytes: int = 2 * 1024 * 1024
    core_intake_attachment_total_max_bytes: int = 10 * 1024 * 1024
    core_intake_attachment_mimes: list[str] = Field(
        default_factory=lambda: ["application/pdf", "image/jpeg", "image/png"]
    )
    # Our service codes -> Core LAS's. Codes not listed are sent unchanged; Core rejects what it
    # does not offer.
    core_service_code_map: dict[str, str] = Field(
        default_factory=lambda: {
            "DPR_GEN": "DPR_GENERATION",
            "QUOTATION_CHECK": "DOC_VERIFICATION",
        }
    )

    @field_validator("database_url", "test_database_admin_url")
    @classmethod
    def _asyncpg(cls, v: str | None) -> str | None:
        return to_async_url(v) if v else v

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
