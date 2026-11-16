from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ---- WhatsApp ----
    meta_app_secret: str
    wa_verify_token: str
    wa_access_token: str
    wa_phone_number_id: str
    wa_api_version: str = "v21.0"
    allowed_numbers: str = ""

    # ---- Infra ----
    redis_url: str = "redis://127.0.0.1:6379"
    mongodb_uri: str = "mongodb://127.0.0.1:27017"
    mongodb_db: str = "research_agent"
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_api_key: str = ""
    qdrant_collection: str = "chunks"

    # ---- LLM ----
    groq_api_key: str = ""
    model_fast: str = "groq/openai/gpt-oss-20b"
    model_main: str = "groq/llama-3.3-70b-versatile"
    model_strong: str = "groq/llama-3.3-70b-versatile"
    model_fallback: str = "groq/openai/gpt-oss-20b"
    llm_concurrency: int = 2
    llm_retries: int = 3

    # ---- Embeddings ----
    embedding_model: str = "gemini/gemini-embedding-001"
    embedding_api_key: str = ""
    embedding_batch: int = 32
    embedding_concurrency: int = 2

    # ---- Research behaviour ----
    max_sub_questions: int = 3
    max_critic_loops: int = 1
    researcher_max_steps: int = 3
    retrieve_k: int = 10
    chunk_size: int = 1000
    chunk_overlap: int = 150
    max_chunks_per_page: int = 20
    writer_max_evidence: int = 30
    openalex_email: str = ""

    # ---- Per-job budget ----
    budget_max_searches: int = 30
    budget_max_fetches: int = 30
    budget_max_llm_calls: int = 90
    budget_max_seconds: int = 900

    # ---- Per-user limits ----
    max_messages_per_day: int = 30

    log_level: str = "INFO"

    @property
    def allowed_numbers_set(self) -> set[str]:
        return {n.strip().lstrip("+") for n in self.allowed_numbers.split(",") if n.strip()}

    def require_worker_keys(self) -> None:
        missing = []
        if not self.groq_api_key:
            missing.append("GROQ_API_KEY")
        if not self.embedding_api_key:
            missing.append("EMBEDDING_API_KEY")
        if missing:
            from app.errors import ConfigError
            raise ConfigError(f"Missing in .env: {', '.join(missing)}")


settings = Settings()
