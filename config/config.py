import os

from dotenv import load_dotenv

load_dotenv()


class Config:
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    EXTRACTION_MODEL: str = os.getenv("EXTRACTION_MODEL", "gpt-4o-mini")
    COMPLIANCE_MODEL: str = os.getenv("COMPLIANCE_MODEL", "gpt-4o-mini")
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
    MAX_TOKENS_PER_CHUNK: int = int(os.getenv("MAX_TOKENS_PER_CHUNK", "10000"))
    STR_BATCH_SIZE: int = int(os.getenv("STR_BATCH_SIZE", "6"))

    # Proactive token-budget pacing (see app/services/rate_limiter.py).
    # Defaults to OpenAI Tier 1 gpt-4o-mini — 200K TPM. Override per deployment.
    OPENAI_TPM_LIMIT: int = int(os.getenv("OPENAI_TPM_LIMIT", "200000"))

    # API server config
    ALLOWED_ORIGINS: str = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000")
    INTERNAL_API_TOKEN: str = os.getenv("INTERNAL_API_TOKEN", "")

    # Supabase. Names match the FE (.env shared with nextjs-fe):
    # NEXT_PUBLIC_SUPABASE_URL is FE-prefixed but the URL is identical for server use.
    # Plain SUPABASE_URL is accepted as an override for non-FE deployments.
    SUPABASE_URL: str = os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL", "")
    SUPABASE_SERVICE_ROLE_KEY: str = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

    # Epic 2 (RAG) toggle. When true, both compliance flows default to
    # RAGChecker (retrieves real STR text from vector_store/) instead of
    # BasicChecker. Requires `uv sync --extra rag` and a populated
    # vector_store/ (run scripts/ingest_str.py). See python-be-e6n.7.
    USE_RAG: bool = os.getenv("USE_RAG", "false").lower() == "true"
    RAG_TOP_K: int = int(os.getenv("RAG_TOP_K", "5"))

    # Epic 3 (multi-agent) toggle. When true, both compliance flows default
    # to AgentOrchestrator — items are routed to specialized agents
    # (structural / fire_safety / sanitary / energy / documents). Items no
    # agent claims fall back to BasicChecker / RAGChecker (per USE_RAG), so
    # laws and non-HN standards still get a verdict. USE_AGENTS overrides
    # USE_RAG at the flow level: the orchestrator's own fallback already
    # respects USE_RAG when picking the unmapped-item checker.
    USE_AGENTS: bool = os.getenv("USE_AGENTS", "false").lower() == "true"

    @classmethod
    def validate(cls) -> bool:
        return bool(cls.OPENAI_API_KEY)

    @classmethod
    def allowed_origins(cls) -> list[str]:
        return [origin.strip() for origin in cls.ALLOWED_ORIGINS.split(",") if origin.strip()]
