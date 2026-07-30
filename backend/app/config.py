from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM
    anthropic_api_key: str = ""
    llm_model: str = "claude-opus-5"
    # Titles get the strongest model available. Five titles is a ~2k-token call,
    # so the premium is cents; the script is the one that would actually cost
    # something, and it stays on llm_model.
    titles_model: str = "claude-fable-5"

    # TTS
    tts_provider: str = "fake"
    tts_api_key: str = ""
    tts_base_url: str = ""

    # Self-hosted TTS (TTS_PROVIDER=local). The server runs outside this
    # Compose stack, so the default reaches back out to the host machine.
    tts_local_url: str = "http://host.docker.internal:8004"
    # Generation knobs, passed through per request. Defaults match the
    # server's own; raise exaggeration for a more theatrical read, lower
    # temperature for a steadier, less surprising one.
    tts_local_temperature: float = 0.8
    tts_local_exaggeration: float = 0.5
    tts_local_cfg_weight: float = 0.5
    tts_local_chunk_size: int = 300

    # Storage
    # Where the API reaches object storage (a Docker-internal host in Compose).
    s3_endpoint_url: str = "http://localhost:9000"
    # Where a *browser* reaches it. Presigned URLs are signed against this, so
    # they must use a host the user's machine can resolve. Blank = same as above.
    s3_public_endpoint_url: str = ""
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "scriptcast"
    s3_region: str = "us-east-1"

    # Auth
    app_token: str = "dev-local-token"

    # Comma-separated browser origins allowed to call the API.
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    # Infra
    database_url: str = "postgresql+asyncpg://scriptcast:scriptcast@localhost:5432/scriptcast"
    sync_database_url: str = "postgresql+psycopg://scriptcast:scriptcast@localhost:5432/scriptcast"
    redis_url: str = "redis://localhost:6379/0"

    # Pipeline tuning
    title_count: int = 5
    # Script length is requested in minutes of narration, because that is the
    # unit the user actually cares about. Words are derived from it: 150 wpm is
    # a common measured pace for clear educational narration.
    words_per_minute: int = 150
    default_script_minutes: int = 8
    min_script_minutes: int = 1
    max_script_minutes: int = 120


@lru_cache
def get_settings() -> Settings:
    return Settings()
