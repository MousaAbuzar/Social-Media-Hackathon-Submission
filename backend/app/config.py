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
    # Generation knobs, passed through per request. These are the house style,
    # not the server's defaults: steadier than stock (0.8) so a long narration
    # does not drift in delivery between chunks, and more expressive than stock
    # (0.5) so it carries a documentary read rather than a flat one.
    tts_local_temperature: float = 0.6
    tts_local_exaggeration: float = 0.85
    tts_local_cfg_weight: float = 0.5
    # 1.0 is unaltered pace. The server resamples anything else after
    # generation, which costs quality, so pace belongs in words_per_minute.
    tts_local_speed_factor: float = 1.0
    tts_local_chunk_size: int = 300
    # How much script goes in one HTTP request. The server chunks internally
    # too, but inside a single call — a 30-minute script was one request held
    # open for over an hour, and anything that interrupted it lost the whole
    # synthesis. This caps a request at a few minutes so a failure costs one
    # piece and can be retried.
    tts_local_request_chars: int = 2_000

    # Starting guess for how fast synthesis runs, used only to show a countdown
    # while the TTS stage works. 5 chars/sec is what a laptop RTX 4050 measured
    # on a cloned voice; it is replaced by the machine's own observed rate as
    # soon as one run has finished, so the default only has to be in the right
    # order of magnitude.
    tts_chars_per_second: float = 5.0

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

    # Hard ceiling on what one run may spend, in US dollars, across every
    # stage and both providers. The runner refuses to start a stage it cannot
    # afford, the LLM adapter aborts mid-call if a retry loop crosses the line,
    # and the run fails the moment spend exceeds it. Nothing here is advisory.
    run_budget_usd: float = 3.00

    @property
    def run_budget_micros(self) -> int:
        return round(self.run_budget_usd * 1_000_000)

    # Pipeline tuning
    title_count: int = 5
    # Script length is requested in minutes of narration, because that is the
    # unit the user actually cares about. Words are derived from it: 150 wpm is
    # a common measured pace for clear educational narration.
    words_per_minute: int = 150
    default_script_minutes: int = 8
    # Ground the script in live sources — what Neil deGrasse Tyson has said
    # publicly on the topic, plus current figures and recent results. Costs
    # $10 per 1,000 searches on top of tokens, and adds a minute or two to the
    # stage. Set false to write from the model's own knowledge instead.
    script_web_search: bool = True
    # Ceiling on searches per script. Enough to cover a topic from several
    # angles; low enough that a runaway research loop can't quietly cost real
    # money.
    web_search_max_uses: int = 8
    min_script_minutes: int = 1
    max_script_minutes: int = 120


@lru_cache
def get_settings() -> Settings:
    return Settings()
