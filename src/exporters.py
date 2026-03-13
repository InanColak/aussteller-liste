from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook

# Match the same illegal characters openpyxl checks for
_ILLEGAL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\ufffe\uffff]")

from src.config import OUTPUT_DIR
from src.models import ScrapeResult

CACHE_DIR = OUTPUT_DIR / "cache"
CACHE_DIR.mkdir(exist_ok=True)

FIELD_NAMES = [
    "company_name",
    "website",
    "hall",
    "stand",
    "country",
    "city",
    "categories",
    "description",
    "phone",
    "email",
    "address",
]


def _sanitize_filename(name: str) -> str:
    base = "".join(c if c.isalnum() or c in "-_ " else "_" for c in name).strip()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{base}_{ts}"


def _row(exhibitor_dict: dict) -> list[str]:
    row = []
    for field in FIELD_NAMES:
        val = exhibitor_dict.get(field, "")
        if isinstance(val, list):
            val = ", ".join(val)
        val = str(val) if val else ""
        val = _ILLEGAL_CHARS_RE.sub("", val)
        row.append(val)
    return row


def save_cache(result: ScrapeResult) -> Path:
    """Save scraped data as JSON cache so it can be re-exported without re-scraping."""
    base = "".join(c if c.isalnum() or c in "-_ " else "_" for c in result.fair_name).strip()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = CACHE_DIR / f"{base}_{ts}.json"
    dest.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return dest


def load_cache(path: Path) -> ScrapeResult:
    """Load a cached ScrapeResult from JSON."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return ScrapeResult.model_validate(data)


def list_caches() -> list[Path]:
    """List all cached JSON files."""
    return sorted(CACHE_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)


def export_csv(result: ScrapeResult, output_dir: Path | None = None) -> Path:
    dest = (output_dir or OUTPUT_DIR) / f"{_sanitize_filename(result.fair_name)}.csv"
    with dest.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(FIELD_NAMES)
        for ex in result.exhibitors:
            writer.writerow(_row(ex.model_dump()))
    return dest


def export_excel(result: ScrapeResult, output_dir: Path | None = None) -> Path:
    dest = (output_dir or OUTPUT_DIR) / f"{_sanitize_filename(result.fair_name)}.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Exhibitors"
    ws.append(FIELD_NAMES)
    for ex in result.exhibitors:
        ws.append(_row(ex.model_dump()))
    # Auto-width columns
    for col in ws.columns:
        max_len = max((len(str(cell.value or "")) for cell in col), default=10)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 2, 50)
    wb.save(dest)
    return dest
