from __future__ import annotations

from abc import ABC, abstractmethod

from src.models import ScrapeResult


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
    async def scrape(self, url: str, limit: int = 0) -> ScrapeResult:
        """Scrape exhibitors from the given URL.

        Args:
            url: The fair/exhibitor page URL.
            limit: Max exhibitors to return (0 = all).
        """
