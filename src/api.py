from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, date
from enum import Enum

import jwt
from fastapi import FastAPI, HTTPException, Depends, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from src.config import (
    OUTPUT_DIR,
    LOG_DIR,
    MAX_CONCURRENT_JOBS,
    DAILY_SCRAPE_LIMIT,
    SCRAPE_TIMEOUT,
    API_KEY,
    ALLOWED_ORIGINS,
    DATABASE_URL,
    DOWNLOAD_TOKEN_SECRET,
    DOWNLOAD_TOKEN_TTL,
    PUBLIC_BASE_URL,
)
from src.exporters import export_csv, export_excel
from src.orchestrator import scrape_url

# --- Logging ---

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "scraper.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("aussteller-api")

# --- App ---

_start_time = time.time()

app = FastAPI(
    title="Aussteller Scraper API",
    description="Trade fair exhibitor list scraper API",
    version="0.2.0",
)

# --- CORS Middleware ---

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Auth ---


async def verify_api_key(request: Request) -> None:
    """Dependency that checks X-API-Key header. Disabled when API_KEY is empty."""
    if not API_KEY:
        return
    key = request.headers.get("X-API-Key", "")
    if key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


# --- Models ---


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"


class ScrapeRequest(BaseModel):
    url: str = Field(..., description="Trade fair URL to scrape")
    format: str = Field("excel", pattern="^(excel|csv)$", description="Output format: excel or csv")
    limit: int = Field(0, ge=0, description="Max exhibitors (0 = all)")


class JobInfo(BaseModel):
    job_id: str
    status: JobStatus
    created_at: datetime
    completed_at: datetime | None = None
    url: str
    format: str
    limit: int = 0
    total_exhibitors: int = 0
    error: str | None = None
    file_name: str | None = None
    progress: str | None = None


# --- In-memory job store ---

jobs: dict[str, JobInfo] = {}

# --- Rate limiting ---

_semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)
_daily_count: dict[str, int] = {}  # date string -> count


def _check_daily_limit() -> bool:
    today = date.today().isoformat()
    return _daily_count.get(today, 0) < DAILY_SCRAPE_LIMIT


def _increment_daily_count() -> int:
    today = date.today().isoformat()
    # Clean old dates
    for key in list(_daily_count):
        if key != today:
            del _daily_count[key]
    _daily_count[today] = _daily_count.get(today, 0) + 1
    return _daily_count[today]


def _get_running_job_count() -> int:
    return sum(1 for j in jobs.values() if j.status in (JobStatus.queued, JobStatus.running))


# --- Background worker ---


async def _run_scrape_job(job_id: str) -> None:
    job = jobs[job_id]

    async with _semaphore:
        job.status = JobStatus.running
        job.progress = "Scraping started"
        logger.info("Job %s started — URL: %s (limit: %d)", job_id, job.url, job.limit)

        async def update_progress(count: int, message: str) -> None:
            job.total_exhibitors = count
            job.progress = message

        try:
            result = await asyncio.wait_for(
                scrape_url(job.url, limit=job.limit, progress_callback=update_progress),
                timeout=SCRAPE_TIMEOUT,
            )

            if result.total_exhibitors == 0:
                job.status = JobStatus.failed
                job.completed_at = datetime.now()
                job.error = "No exhibitors found on this URL"
                job.progress = "Failed: no exhibitors found"
                logger.warning("Job %s — no exhibitors found: %s", job_id, job.url)
                return

            job.progress = "Exporting results"
            if job.format == "csv":
                path = export_csv(result)
            else:
                path = export_excel(result)

            # Save to database if configured
            if DATABASE_URL:
                try:
                    from src.database import save_to_db

                    job.progress = "Saving to database"
                    db_count = await save_to_db(result)
                    logger.info("Job %s — saved %d companies to database", job_id, db_count)
                except Exception as e:
                    logger.warning("Job %s — database save failed: %s", job_id, e)

            job.status = JobStatus.completed
            job.completed_at = datetime.now()
            job.total_exhibitors = result.total_exhibitors
            job.file_name = path.name
            job.progress = "Completed"
            logger.info("Job %s completed — %d exhibitors → %s", job_id, result.total_exhibitors, path.name)

        except asyncio.TimeoutError:
            job.status = JobStatus.failed
            job.completed_at = datetime.now()
            job.error = f"Scrape timed out after {SCRAPE_TIMEOUT} seconds"
            job.progress = "Failed: timeout"
            logger.error("Job %s timed out after %ds — URL: %s", job_id, SCRAPE_TIMEOUT, job.url)

        except Exception as e:
            job.status = JobStatus.failed
            job.completed_at = datetime.now()
            job.error = f"{type(e).__name__}: {e}"
            job.progress = f"Failed: {type(e).__name__}"
            logger.error("Job %s failed — %s: %s", job_id, type(e).__name__, e)


# --- Lifecycle ---


@app.on_event("startup")
async def startup_event() -> None:
    if DATABASE_URL:
        from src.database import init_db

        try:
            await init_db()
            logger.info("Database connection established.")
        except Exception as e:
            logger.warning("Database init failed (will retry on save): %s", e)


@app.on_event("shutdown")
async def shutdown_event() -> None:
    if DATABASE_URL:
        from src.database import close_db

        await close_db()


# --- Endpoints ---


@app.get("/health")
async def health() -> dict:
    today = date.today().isoformat()
    uptime_seconds = int(time.time() - _start_time)
    return {
        "status": "ok",
        "version": app.version,
        "uptime_seconds": uptime_seconds,
        "running_jobs": _get_running_job_count(),
        "daily_scrapes": _daily_count.get(today, 0),
        "daily_limit": DAILY_SCRAPE_LIMIT,
        "max_concurrent": MAX_CONCURRENT_JOBS,
    }


@app.post("/scrape", response_model=JobInfo, dependencies=[Depends(verify_api_key)])
async def start_scrape(request: ScrapeRequest) -> JobInfo:
    # Check daily limit
    if not _check_daily_limit():
        raise HTTPException(
            status_code=429,
            detail=f"Daily scrape limit reached ({DAILY_SCRAPE_LIMIT}). Try again tomorrow.",
        )

    count = _increment_daily_count()
    logger.info("Daily scrape count: %d/%d", count, DAILY_SCRAPE_LIMIT)

    job_id = uuid.uuid4().hex[:12]
    job = JobInfo(
        job_id=job_id,
        status=JobStatus.queued,
        created_at=datetime.now(),
        url=request.url,
        format=request.format,
        limit=request.limit,
    )
    jobs[job_id] = job
    logger.info("Job %s queued — URL: %s, format: %s, limit: %d", job_id, request.url, request.format, request.limit)

    asyncio.create_task(_run_scrape_job(job_id))

    return job


class SyncScrapeRequest(BaseModel):
    url: str = Field(..., description="Trade fair URL to scrape")
    format: str = Field("excel", pattern="^(excel|csv)$", description="Output format: excel or csv")
    limit: int = Field(0, ge=0, description="Max exhibitors (0 = all)")


class SyncScrapeResponse(BaseModel):
    status: str
    url: str
    total_exhibitors: int = 0
    file_name: str | None = None
    fair_name: str | None = None
    exhibitors: list[dict] = Field(default_factory=list)
    error: str | None = None


@app.post("/scrape/sync", response_model=SyncScrapeResponse, dependencies=[Depends(verify_api_key)])
async def sync_scrape(request: SyncScrapeRequest) -> SyncScrapeResponse:
    """Synchronous scrape — waits for completion and returns results directly."""
    if not _check_daily_limit():
        raise HTTPException(
            status_code=429,
            detail=f"Daily scrape limit reached ({DAILY_SCRAPE_LIMIT}). Try again tomorrow.",
        )

    _increment_daily_count()
    logger.info("Sync scrape started — URL: %s, format: %s, limit: %d", request.url, request.format, request.limit)

    try:
        result = await asyncio.wait_for(
            scrape_url(request.url, limit=request.limit),
            timeout=SCRAPE_TIMEOUT,
        )

        if result.total_exhibitors == 0:
            return SyncScrapeResponse(status="failed", url=request.url, error="No exhibitors found on this URL")

        if request.format == "csv":
            path = export_csv(result)
        else:
            path = export_excel(result)

        # Save to database if configured
        if DATABASE_URL:
            try:
                from src.database import save_to_db
                await save_to_db(result)
            except Exception as e:
                logger.warning("Sync scrape — database save failed: %s", e)

        logger.info("Sync scrape completed — %d exhibitors → %s", result.total_exhibitors, path.name)

        return SyncScrapeResponse(
            status="completed",
            url=request.url,
            total_exhibitors=result.total_exhibitors,
            file_name=path.name,
            fair_name=result.fair_name,
            exhibitors=[ex.model_dump(exclude_none=True) for ex in result.exhibitors],
        )

    except asyncio.TimeoutError:
        return SyncScrapeResponse(status="failed", url=request.url, error=f"Scrape timed out after {SCRAPE_TIMEOUT} seconds")

    except Exception as e:
        logger.error("Sync scrape failed — %s: %s", type(e).__name__, e)
        return SyncScrapeResponse(status="failed", url=request.url, error=f"{type(e).__name__}: {e}")


@app.get("/scrape/{job_id}/status", response_model=JobInfo, dependencies=[Depends(verify_api_key)])
async def get_status(job_id: str) -> JobInfo:
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return jobs[job_id]


@app.get("/scrape/jobs/recent", response_model=list[JobInfo], dependencies=[Depends(verify_api_key)])
async def list_recent_jobs(limit: int = 20) -> list[JobInfo]:
    ordered = sorted(jobs.values(), key=lambda j: j.created_at, reverse=True)
    return ordered[: max(1, min(limit, 100))]


@app.get("/scrape/{job_id}/download-url", dependencies=[Depends(verify_api_key)])
async def get_download_url(job_id: str) -> dict:
    if not DOWNLOAD_TOKEN_SECRET or not PUBLIC_BASE_URL:
        raise HTTPException(status_code=503, detail="download-url feature not configured")

    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status != JobStatus.completed:
        raise HTTPException(status_code=409, detail=f"Job is {job.status.value}, not completed")

    now = int(time.time())
    token = jwt.encode(
        {"sub": job_id, "aud": "download", "iat": now, "exp": now + DOWNLOAD_TOKEN_TTL},
        DOWNLOAD_TOKEN_SECRET,
        algorithm="HS256",
    )
    return {
        "url": f"{PUBLIC_BASE_URL}/scrape/{job_id}/download?token={token}",
        "expires_in_seconds": DOWNLOAD_TOKEN_TTL,
        "filename": job.file_name,
    }


@app.get("/scrape/{job_id}/download")
async def download_result(
    job_id: str,
    token: str | None = None,
    x_api_key: str | None = Header(default=None),
) -> FileResponse:
    if token:
        if not DOWNLOAD_TOKEN_SECRET:
            raise HTTPException(status_code=503, detail="token auth not configured")
        try:
            payload = jwt.decode(
                token,
                DOWNLOAD_TOKEN_SECRET,
                algorithms=["HS256"],
                audience="download",
            )
        except jwt.PyJWTError:
            raise HTTPException(status_code=401, detail="invalid or expired token")
        if payload.get("sub") != job_id:
            raise HTTPException(status_code=401, detail="token does not match job")
    else:
        if API_KEY and x_api_key != API_KEY:
            raise HTTPException(status_code=401, detail="Invalid or missing API key")

    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]

    if job.status != JobStatus.completed:
        raise HTTPException(status_code=400, detail=f"Job is {job.status.value}, not completed")

    if not job.file_name:
        raise HTTPException(status_code=500, detail="No output file")

    file_path = OUTPUT_DIR / job.file_name
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    media_type = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if file_path.suffix == ".xlsx"
        else "text/csv"
    )

    return FileResponse(
        path=file_path,
        filename=job.file_name,
        media_type=media_type,
    )
