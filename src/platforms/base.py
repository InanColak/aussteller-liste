from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

from src.models import ScrapeResult

type ProgressCallback = Callable[[int, str], Awaitable[None]] | None


class BaseScraper(ABC):
    """Base class for all platform-specific scrapers."""

    name: str = "base"
    description: str = ""
    url_patterns: list[str] = []

    @classmethod
    @abstractmethod
    def detect(cls, url: str) -> bool:
        """Return True if this scraper can handle the given URL."""

    @abstractmethod
    async def scrape(self, url: str, limit: int = 0, progress_callback: ProgressCallback = None) -> ScrapeResult:
        """Scrape exhibitors from the given URL.

        Args:
            url: The fair/exhibitor page URL.
            limit: Max exhibitors to return (0 = all).
            progress_callback: Optional async callback(count, message) for live progress.
        """
