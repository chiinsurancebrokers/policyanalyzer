"""
Central configuration. All values come from environment variables
(set via .env locally, or Railway's Variables tab in production).
"""
import os
from dataclasses import dataclass, field


@dataclass
class Config:
    # AI — primary extraction
    ANTHROPIC_API_KEY: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    CLAUDE_MODEL: str = field(default_factory=lambda: os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001"))

    # AI — optional second-opinion verification (same pattern as Asklepios/KiraAIpet)
    OPENAI_API_KEY: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    GPT_MODEL: str = field(default_factory=lambda: os.getenv("GPT_MODEL", "gpt-4o-mini"))

    # Processing
    MAX_FILE_SIZE_MB: int = int(os.getenv("MAX_FILE_SIZE_MB", "10"))
    SUPPORTED_EXTENSIONS: tuple = (".pdf", ".txt")

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
