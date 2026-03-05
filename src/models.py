from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Exhibitor(BaseModel):
    company_name: str
    website: str | None = None
    hall: str | None = None
    stand: str | None = None
    country: str | None = None
    city: str | None = None
    categories: list[str] = Field(default_factory=list)
    description: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None


class ScrapeResult(BaseModel):
    fair_name: str
    fair_url: str
    scraped_at: datetime = Field(default_factory=datetime.now)
    total_exhibitors: int = 0
    exhibitors: list[Exhibitor] = Field(default_factory=list)

    def model_post_init(self, __context: object) -> None:
        if self.total_exhibitors == 0:
            self.total_exhibitors = len(self.exhibitors)
