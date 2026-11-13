from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Reads from environment variables and an optional .env file.

    Keys default to empty so the app boots without credentials; each
    integration checks for its own key when it is first used. Any non-empty
    value counts as present.
    """

    # An empty value in .env or the environment (KEY=) means "not set": the default applies.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"

    supabase_url: str = ""
    # Server-side key: a new secret key (sb_secret_...) or a legacy service_role key.
    supabase_service_key: SecretStr = SecretStr("")
    database_url: SecretStr = SecretStr("")

    deepgram_api_key: SecretStr = SecretStr("")
    deepgram_model: str = "nova-3"
    deepgram_base_url: str = "https://api.deepgram.com"
    # Brand and product names Deepgram should listen for, comma-separated. Added to every call.
    deepgram_keyterms: str = ""
    # Files sent straight to POST /calls. Vercel Functions accept request bodies up to 4.5 MB, so set 4
    # there; bigger files go through Storage (below) or a URL.
    max_upload_mb: int = 100
    # Large audio: the browser uploads straight to this private Supabase Storage bucket with a signed
    # upload URL, and Deepgram fetches the file with a short-lived signed download URL.
    storage_bucket: str = "call-audio"
    storage_max_mb: int = 50  # Supabase free plan: 50 MB per file
    storage_signed_url_seconds: int = 3600
    # Playback on the call page: the player gets a signed URL this short-lived, and asks for a new one when it expires.
    audio_url_seconds: int = 300
    # Deepgram price per audio minute, for cost reporting in evals. Empty = not reported.
    deepgram_price_per_min: float | None = None

    llm_provider: Literal["anthropic", "openai", "gemini"] = "anthropic"
    anthropic_api_key: SecretStr = SecretStr("")
    # Sonnet: scoring is judgment work over the whole call.
    anthropic_model: str = "claude-sonnet-5-5"
    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = ""  # required when LLM_PROVIDER=openai
    openai_base_url: str = "https://api.openai.com"
    gemini_api_key: SecretStr = SecretStr("")
    # Flash: call scoring is judgment work, so the Sonnet-class Gemini model.
    gemini_model: str = "gemini-3.8-flash"
    gemini_base_url: str = "https://generativelanguage.googleapis.com"
    llm_max_attempts: int = 3
    # Price per million tokens for models without a built-in price (e.g. Gemini, OpenAI). Empty = cost not recorded.
    llm_price_input_per_mtok: float | None = None
    llm_price_output_per_mtok: float | None = None

    # background: reply at once, then transcribe and score (local and always-on containers).
    # inline: transcribe and score inside the request, for hosts that throttle CPU after the
    # response (Cloud Run with request-based billing).
    processing_mode: Literal["background", "inline"] = "background"

    # One passcode for uploads, scoring and the dashboard. Empty = those routes stay closed.
    app_passcode: SecretStr = SecretStr("")
    # Failed-login limit: "database" keeps it in Postgres (login_failures) so it holds across serverless
    # instances; "memory" is per process. "auto" uses the database when Supabase is configured.
    login_limit_store: Literal["auto", "memory", "database"] = "auto"
    # Header with the client address set by the host's proxy (Vercel: x-real-ip). Empty = the socket
    # address. Only set it behind a proxy that overwrites the header, or clients can fake it.
    client_ip_header: str = ""
    session_hours: int = 12
    # Signs the session cookie. Any long random string. Empty = the key is derived from the passcode alone,
    # which lets someone who captured a cookie test passcode guesses offline.
    session_secret: SecretStr = SecretStr("")
    # Reads without the passcode (demo list, call pages, call JSON, audio links): requests per client address
    # per minute, counted per running instance. 0 = no limit.
    public_reads_per_minute: int = 60
    # Read-only access to calls marked is_demo for visitors without the passcode.
    demo_mode: bool = False
    # "Back to portfolio" link in the header.
    portfolio_url: str = "https://syed-tajdar.vercel.app"


@lru_cache
def get_settings() -> Settings:
    return Settings()
