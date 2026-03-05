from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook

from src.config import OUTPUT_DIR
from src.models import ScrapeResult

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
        row.append(str(val) if val else "")
    return row


def export_csv(result: ScrapeResult, output_dir: Path | None = None) -> Path:
    dest = (output_dir or OUTPUT_DIR) / f"{_sanitize_filename(result.fair_name)}.csv"
    with dest.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
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
