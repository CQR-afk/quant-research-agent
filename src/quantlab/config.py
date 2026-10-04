"""Configuration loaded from environment variables and an optional local .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    default_ticker: str = os.getenv("DEFAULT_TICKER", "SPY")
    data_period: str = os.getenv("DATA_PERIOD", "5y")
    output_dir: Path = Path(os.getenv("OUTPUT_DIR", "data/processed"))
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY") or None
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-6-astra")

    @property
    def resolved_output_dir(self) -> Path:
        path = self.output_dir
        return path if path.is_absolute() else PROJECT_ROOT / path


settings = Settings()

