"""
Central configuration. All values come from environment variables
(set via .env locally, or Railway's Variables tab in production).
"""
import os
from dataclasses import dataclass, field


@dataclass
class Config:
    # AI — primary extraction. Haiku 4.5 for cost efficiency. Haiku 4.5 does
    # NOT support the `effort` parameter (per Anthropic's docs, effort is only
    # available on Opus/Sonnet-tier models) — but it's the first Haiku model
    # to support extended thinking, which is the equivalent lever here: a
    # token budget for internal reasoning before it answers, raising
    # consistency/accuracy without moving to a pricier model tier.
    ANTHROPIC_API_KEY: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    CLAUDE_MODEL: str = field(default_factory=lambda: os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001"))

    # AI — HAL recommendation. Same model + extended thinking approach.
    HAL_MODEL: str = field(default_factory=lambda: os.getenv("HAL_MODEL", "claude-haiku-4-5-20251001"))

    # Extended thinking token budget for both Claude calls above. Only takes
    # effect on models that support extended thinking (Haiku 4.5 does).
    # Increase for more thorough reasoning at the cost of latency/tokens.
    THINKING_BUDGET_TOKENS: int = int(os.getenv("THINKING_BUDGET_TOKENS", "3000"))

    # AI — optional second-opinion verification (same pattern as Asklepios/KiraAIpet)
    OPENAI_API_KEY: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    GPT_MODEL: str = field(default_factory=lambda: os.getenv("GPT_MODEL", "gpt-4o-mini"))

    # Processing
    MAX_FILE_SIZE_MB: int = int(os.getenv("MAX_FILE_SIZE_MB", "10"))
    SUPPORTED_EXTENSIONS: tuple = (".pdf", ".txt")

    # Max combined character budget sent to the AI per policy. Claude's context
    # window comfortably fits far more than earlier defaults assumed — a low
    # budget here silently truncates long wording documents before reaching
    # sections like general exclusions, which is exactly where the clause-level
    # critical limitations this tool exists to catch usually live.
    MAX_EXTRACTION_CHARS: int = int(os.getenv("MAX_EXTRACTION_CHARS", "150000"))

    # Known providers for quick name detection (extend freely)
    KNOWN_PROVIDERS: tuple = (
        "Bupa", "Cigna", "April", "IMG", "Allianz", "AXA PPP",
        "Morgan Price", "Europ Assistance", "Now Health",
        "Aetna", "William Russell", "GeoBlue",
    )

    # Cache
    CACHE_DB_PATH: str = field(default_factory=lambda: os.getenv("CACHE_DB_PATH", "data/cache.db"))
    CACHE_ENABLED: bool = os.getenv("CACHE_ENABLED", "true").lower() == "true"

    @property
    def max_file_size_bytes(self) -> int:
        return self.MAX_FILE_SIZE_MB * 1_000_000

    @property
    def has_ai(self) -> bool:
        return bool(self.ANTHROPIC_API_KEY)

    @property
    def has_gpt_verification(self) -> bool:
        return bool(self.OPENAI_API_KEY)


config = Config()
