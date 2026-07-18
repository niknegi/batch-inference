from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    api_keys: str = Field(default="dev-api-key-change-me", alias="API_KEYS")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # Build / deploy identity (set by CD or compose)
    git_sha: str = Field(default="", alias="GIT_SHA")
    build_id: str = Field(default="", alias="BUILD_ID")
    built_at: str = Field(default="", alias="BUILT_AT")

    database_url: str = Field(
        default="postgresql+asyncpg://batch:batch@localhost:5432/batch_inference",
        alias="DATABASE_URL",
    )
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")

    spaces_endpoint_url: str = Field(default="http://localhost:9000", alias="SPACES_ENDPOINT_URL")
    # Optional public/base URL for presigned links only (e.g. http://DROPLET_IP:9000).
    # Keep SPACES_ENDPOINT_URL as the internal Docker hostname for server-side S3 ops.
    spaces_public_endpoint_url: str | None = Field(default=None, alias="SPACES_PUBLIC_ENDPOINT_URL")
    spaces_access_key: str = Field(default="minioadmin", alias="SPACES_ACCESS_KEY")
    spaces_secret_key: str = Field(default="minioadmin", alias="SPACES_SECRET_KEY")
    spaces_bucket: str = Field(default="batch-inference", alias="SPACES_BUCKET")
    spaces_region: str = Field(default="us-east-1", alias="SPACES_REGION")
    spaces_force_path_style: bool = Field(default=True, alias="SPACES_FORCE_PATH_STYLE")
    # Optional public API base (e.g. http://DROPLET_IP:8000) for webhook result_url when MinIO stays private.
    public_base_url: str | None = Field(default=None, alias="PUBLIC_BASE_URL")

    worker_concurrency: int = Field(default=32, alias="WORKER_CONCURRENCY")
    default_chunk_size: int = Field(default=100, alias="DEFAULT_CHUNK_SIZE")
    chunk_lease_seconds: int = Field(default=300, alias="CHUNK_LEASE_SECONDS")
    chunk_max_attempts: int = Field(default=5, alias="CHUNK_MAX_ATTEMPTS")
    webhook_max_attempts: int = Field(default=10, alias="WEBHOOK_MAX_ATTEMPTS")

    mock_provider: bool = Field(default=False, alias="MOCK_PROVIDER")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    openai_compatible_base_url: str = Field(default="", alias="OPENAI_COMPATIBLE_BASE_URL")
    openai_compatible_api_key: str = Field(default="", alias="OPENAI_COMPATIBLE_API_KEY")

    # DigitalOcean Serverless Inference (prod LLM endpoint)
    # Create key: Control Panel → Inference → API Keys → Generate Key
    do_inference_api_key: str = Field(default="", alias="DO_INFERENCE_API_KEY")
    do_inference_base_url: str = Field(
        default="https://inference.do-ai.run/v1",
        alias="DO_INFERENCE_BASE_URL",
    )

    default_rate_limit_rps: float = Field(default=50.0, alias="DEFAULT_RATE_LIMIT_RPS")
    default_max_concurrency: int = Field(default=16, alias="DEFAULT_MAX_CONCURRENCY")

    default_provider: str = Field(default="mock", alias="DEFAULT_PROVIDER")
    default_model: str = Field(default="openai-gpt-oss-20b", alias="DEFAULT_MODEL")
    default_cost_preference: str = Field(default="economy", alias="DEFAULT_COST_PREFERENCE")

    @property
    def api_key_set(self) -> set[str]:
        return {k.strip() for k in self.api_keys.split(",") if k.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
