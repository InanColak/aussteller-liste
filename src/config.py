from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# Rate limiting
REQUEST_DELAY: float = float(os.getenv("REQUEST_DELAY", "0.5"))
MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", "3"))

# Scraping defaults
DEFAULT_LIMIT: int = 0  # 0 = no limit
DEFAULT_FORMAT: str = "excel"
