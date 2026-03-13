from __future__ import annotations

from src.platforms.base import BaseScraper
from src.platforms.messe_berlin import MesseBerlinScraper
from src.platforms.messe_duesseldorf import MesseDuesseldorfScraper
from src.platforms.messe_frankfurt import MesseFrankfurtScraper
from src.platforms.ungerboeck import UngerboeckScraper

SCRAPERS: list[type[BaseScraper]] = [
    MesseDuesseldorfScraper,
    MesseBerlinScraper,
    MesseFrankfurtScraper,
    UngerboeckScraper,
]


def detect_platform(url: str) -> BaseScraper | None:
    """Return an instantiated scraper for the URL, or None if unknown."""
    for scraper_cls in SCRAPERS:
        if scraper_cls.detect(url):
            return scraper_cls()
    return None


def list_platforms() -> list[dict[str, str]]:
    """Return info about all registered platform scrapers."""
    return [
        {
            "name": cls.name,
            "description": cls.description,
            "patterns": ", ".join(cls.url_patterns),
        }
        for cls in SCRAPERS
    ]
