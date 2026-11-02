"""Application configuration loaded from environment."""

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

from .path_safety import safe_path_under

load_dotenv()


@dataclass
class Config:
    """Runtime configuration for the scholar scraper."""

    scholar_id: str
    scholar_data_dir: str = field(
        default_factory=lambda: os.environ.get("SCHOLAR_DATA_DIR", "scholar_data")
    )
    cache_dir: str = field(default_factory=lambda: os.environ.get("CACHE_DIR", "cache"))
    cache_expire_seconds: int = field(
        default_factory=lambda: int(os.environ.get("CACHE_EXPIRE_SECONDS", 60 * 60 * 24 * 30))
    )
    # Idempotency: skip full fetch if data is fresh within this many seconds (default 7 days)
    fresh_data_seconds: int = field(
        default_factory=lambda: int(os.environ.get("FRESH_DATA_SECONDS", 60 * 60 * 24 * 7))
    )
    # Retries for scholarly / API calls
    max_retries: int = field(default_factory=lambda: int(os.environ.get("MAX_RETRIES", 3)))
    retry_base_delay_seconds: float = field(
        default_factory=lambda: float(os.environ.get("RETRY_BASE_DELAY", 5))
    )
    # Rate limiting
    coauthor_delay_seconds: float = field(
        default_factory=lambda: float(os.environ.get("COAUTHOR_DELAY", 2))
    )
    publication_delay_seconds: float = field(
        default_factory=lambda: float(os.environ.get("PUBLICATION_DELAY", 1))
    )

    @property
    def output_path(self) -> str:
        """Path to the scholar JSON output file (contained under ``scholar_data_dir``)."""
        base = os.path.abspath(self.scholar_data_dir)
        path = safe_path_under(base, f"{self.scholar_id}.json")
        if path is None:
            raise ValueError(f"Invalid scholar_id for output path: {self.scholar_id!r}")
        return str(path)
