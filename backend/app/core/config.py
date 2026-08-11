"""Application configuration.

All settings come from environment variables (loaded from the repo-root `.env`),
with safe local-first defaults so the app boots with zero configuration.

Nothing in this module is vendor-specific: swapping Ollama for a hosted LLM,
or SQLite for Postgres, is a change to `.env`, not to code.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> repo root is 4 levels up
REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Application ---
    app_env: str = "development"
    log_level: str = "INFO"
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # --- LLM ---
    llm_provider: str = "ollama"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:8b"
    ollama_thinking: bool = False
    ollama_timeout_seconds: float = 90.0
    ollama_keep_alive: str = "30m"
    llm_temperature: float = 0.1
    llm_max_tokens: int = 600
    ollama_router_model: str = ""

    # --- Gemini (optional, free tier; see gemini_provider.py for the
    #     privacy trade-off before enabling on real resident data) ---
    gemini_api_key: str = Field(default="", repr=False)
    gemini_model: str = "gemini-2.0-flash"

    # --- Embeddings ---
    embedding_provider: str = "ollama"
    embedding_model: str = "nomic-embed-text"
    embedding_dim: int = 768

    # --- Vector store ---
    qdrant_url: str = ""
    qdrant_path: str = "./data/qdrant"
    qdrant_collection: str = "garden_city_kb"

    # --- Database ---
    database_url: str = "sqlite+aiosqlite:///./data/gardencity.db"

    # --- Speech to text ---
    stt_provider: str = "local_whisper"
    whisper_model: str = "base.en"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    whisper_beam_size: int = 1

    # --- Text to speech ---
    tts_provider: str = "kokoro"
    piper_voice: str = "en_US-lessac-medium"
    piper_voice_dir: str = "./data/models/piper"
    kokoro_voice: str = "af_heart"
    kokoro_model_dir: str = "./data/models/kokoro"
    kokoro_speed: float = 1.0
    tts_fallback_enabled: bool = True

    # --- RAG ---
    rag_top_k: int = 6
    rag_chunk_size: int = 768
    rag_chunk_overlap: int = 128
    rag_min_score: float = 0.35

    # --- Confidence ---
    confidence_high: float = 0.62
    confidence_medium: float = 0.38
    grounding_check_enabled: bool = True

    # --- Privacy ---
    retention_days: int = 7
    store_audio: bool = False

    # --- Crawler ---
    village_base_url: str = "https://www.gardencityny.net"
    village_sitemap_url: str = "https://www.gardencityny.net/sitemap.xml"
    crawl_enabled: bool = True
    crawl_max_pages: int = 150
    crawl_delay_seconds: float = 1.0
    crawl_user_agent: str = "GardenCityAI-POC/0.1 (local municipal AI prototype)"

    # --- GoGov ---
    gogov_mode: str = "mock"
    gogov_base_url: str = ""
    gogov_api_key: str = Field(default="", repr=False)

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------
    @field_validator("log_level")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def router_model(self) -> str:
        """Model used for intent classification (falls back to the main model)."""
        return self.ollama_router_model.strip() or self.ollama_model

    @property
    def repo_root(self) -> Path:
        return REPO_ROOT

    def resolve(self, relative: str) -> Path:
        """Resolve a possibly-relative config path against the repo root."""
        p = Path(relative).expanduser()
        return p if p.is_absolute() else (REPO_ROOT / p).resolve()

    @property
    def qdrant_storage_path(self) -> Path:
        return self.resolve(self.qdrant_path)

    @property
    def piper_voice_path(self) -> Path:
        return self.resolve(self.piper_voice_dir)

    @property
    def knowledge_dir(self) -> Path:
        return REPO_ROOT / "knowledge"

    @property
    def config_dir(self) -> Path:
        return REPO_ROOT / "config"

    @property
    def cache_dir(self) -> Path:
        return REPO_ROOT / "data" / "cache"

    @property
    def uses_embedded_qdrant(self) -> bool:
        """True when Qdrant runs in-process on disk (no server, no Docker)."""
        return not self.qdrant_url.strip()

    @property
    def resolved_database_url(self) -> str:
        """Make relative SQLite paths absolute so the DB location never depends
        on which directory the process was launched from."""
        prefix = "sqlite+aiosqlite:///"
        if self.database_url.startswith(prefix):
            raw = self.database_url[len(prefix):]
            if not raw.startswith("/"):
                return f"{prefix}{self.resolve(raw)}"
        return self.database_url

    def ensure_directories(self) -> None:
        for path in (
            self.qdrant_storage_path,
            self.piper_voice_path,
            self.cache_dir,
            REPO_ROOT / "data" / "audio",
        ):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
