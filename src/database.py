from __future__ import annotations

import logging
from datetime import datetime

import asyncpg

from src.config import DATABASE_URL
from src.models import Exhibitor, ScrapeResult

logger = logging.getLogger("aussteller-db")

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS companies (
    id              SERIAL PRIMARY KEY,
    company_name    TEXT NOT NULL,
    website         TEXT,
    hall            TEXT,
    stand           TEXT,
    country         TEXT,
    city            TEXT,
    categories      TEXT[],
    description     TEXT,
    phone           TEXT,
    email           TEXT,
    address         TEXT,
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (company_name, website)
);

CREATE TABLE IF NOT EXISTS company_fairs (
    id              SERIAL PRIMARY KEY,
    company_id      INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    fair_name       TEXT NOT NULL,
    fair_url        TEXT NOT NULL,
    seen_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_company_fairs_company_id ON company_fairs(company_id);
"""

UPSERT_COMPANY_SQL = """
INSERT INTO companies (
    company_name, website, hall, stand, country, city,
    categories, description, phone, email, address,
    first_seen_at, last_seen_at
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $12)
ON CONFLICT (company_name, website)
DO UPDATE SET
    hall        = COALESCE(EXCLUDED.hall, companies.hall),
    stand       = COALESCE(EXCLUDED.stand, companies.stand),
    country     = COALESCE(EXCLUDED.country, companies.country),
    city        = COALESCE(EXCLUDED.city, companies.city),
    categories  = CASE WHEN EXCLUDED.categories != '{}' THEN EXCLUDED.categories ELSE companies.categories END,
    description = COALESCE(EXCLUDED.description, companies.description),
    phone       = COALESCE(EXCLUDED.phone, companies.phone),
    email       = COALESCE(EXCLUDED.email, companies.email),
    address     = COALESCE(EXCLUDED.address, companies.address),
    last_seen_at = EXCLUDED.last_seen_at
RETURNING id;
"""

INSERT_FAIR_SQL = """
INSERT INTO company_fairs (company_id, fair_name, fair_url, seen_at)
VALUES ($1, $2, $3, $4);
"""


async def _get_pool() -> asyncpg.Pool:
    """Create and cache a connection pool."""
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is not set. Add it to your .env file, e.g.:\n"
            "DATABASE_URL=postgresql://user:pass@localhost:5432/aussteller"
        )
    return await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)


_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await _get_pool()
    return _pool


async def init_db() -> None:
    """Create tables if they don't exist."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(CREATE_TABLES_SQL)
    logger.info("Database tables initialized.")


async def save_to_db(result: ScrapeResult) -> int:
    """Save exhibitors to the database. Returns the number of saved/updated companies."""
    pool = await get_pool()
    now = datetime.now().astimezone()
    saved = 0

    async with pool.acquire() as conn:
        for exhibitor in result.exhibitors:
            try:
                row = await conn.fetchrow(
                    UPSERT_COMPANY_SQL,
                    exhibitor.company_name,
                    exhibitor.website,
                    exhibitor.hall,
                    exhibitor.stand,
                    exhibitor.country,
                    exhibitor.city,
                    exhibitor.categories or [],
                    exhibitor.description,
                    exhibitor.phone,
                    exhibitor.email,
                    exhibitor.address,
                    now,
                )
                company_id = row["id"]

                await conn.execute(
                    INSERT_FAIR_SQL,
                    company_id,
                    result.fair_name,
                    result.fair_url,
                    now,
                )
                saved += 1
            except Exception as e:
                logger.warning(
                    "Failed to save company %s: %s", exhibitor.company_name, e
                )

    logger.info("Saved %d/%d companies to database.", saved, len(result.exhibitors))
    return saved


async def close_db() -> None:
    """Close the connection pool."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
