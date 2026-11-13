from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment variables, or an optional .env file. Any non-empty key counts as present."""

    # An empty value in .env or the environment (KEY=) means "not set": the default applies.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    # Postgres with pgvector: Supabase (Connect > Transaction pooler, port 6543, for serverless hosts) or a
    # local container. Connections are opened on demand and closed when idle.
    database_url: SecretStr = SecretStr("")
    db_pool_max: int = 3
    db_pool_max_idle_seconds: float = 30
    documents_dir: str = "documents"

    chunk_max_chars: int = 1200
    chunk_overlap_chars: int = 200

    embedding_provider: Literal["gemini"] = "gemini"
    gemini_api_key: SecretStr = SecretStr("")
    gemini_base_url: str = "https://generativelanguage.googleapis.com"
    embedding_model: str = "gemini-embedding-2"
    # Must match vector(768) in the migration.
    embedding_dim: int = 768
    # Gemini accepts up to 100 texts per batchEmbedContents request.
    embedding_batch_size: int = 100
    # Client-side pacing for the free tier; a 429 is still retried with the delay Gemini asks for.
    embedding_requests_per_minute: int = 10

    anthropic_api_key: SecretStr = SecretStr("")
    # Sonnet: answering with exact quotes from several sources is judgment work.
    answer_model: str = "claude-sonnet-5-5"
    answer_effort: Literal["low", "medium", "high"] = "medium"

    # Cross-language retrieval: translate the question for a second keyword search (Haiku-class model).
    query_translation: bool = True
    translation_model: str = "claude-haiku-5-5"
    # At most this many chunks from one document in the results, so multi-document questions get room.
    max_per_document: int = 3

    # Public demo: questions per client address per window, and in total per UTC day.
    # "auto" keeps the limits and the answer cache in Postgres when DATABASE_URL is set (they then hold
    # across serverless instances), otherwise in memory.
    demo_store: Literal["auto", "memory", "postgres"] = "auto"
    demo_questions_per_client: int = 5
    demo_window_seconds: int = 600
    demo_daily_cap: int = 150
    # Repeated questions are answered from memory without counting against the limits.
    answer_cache_size: int = 200
    answer_cache_seconds: int = 86400
    # Header with the client address set by the host's proxy (Vercel: x-real-ip). Empty = socket address.
    client_ip_header: str = ""

    # MCP over streamable HTTP at /mcp on the web app. MCP_BEARER_TOKEN set: private, every request needs
    # the token, no demo limits. Otherwise MCP_PUBLIC=true: public read-only demo without a token, under the
    # same per-visitor limits and daily cap as the page. Neither: no /mcp.
    mcp_public: bool = False
    mcp_bearer_token: SecretStr = SecretStr("")

    # "Back to portfolio" link in the page header.
    portfolio_url: str = "https://syed-tajdar.vercel.app"

    top_k: int = 6
    search_candidates: int = 20
    rrf_k: int = 60


@lru_cache
def get_settings() -> Settings:
    return Settings()
