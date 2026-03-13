from __future__ import annotations

import json
import re
from datetime import datetime
from fnmatch import fnmatch
from pathlib import Path
from urllib.parse import urlparse

from src.learning.models import SiteProfile

PROFILES_DIR = Path(__file__).resolve().parent / "profiles"
PROFILES_DIR.mkdir(exist_ok=True)


def _hostname_from_url(url: str) -> str:
    return urlparse(url).hostname or ""


def find_profile(url: str) -> SiteProfile | None:
    """Find a saved profile that matches the given URL."""
    hostname = _hostname_from_url(url)
    if not hostname:
        return None

    for profile_path in PROFILES_DIR.glob("*.json"):
        try:
            profile = SiteProfile.model_validate_json(profile_path.read_text("utf-8"))
        except Exception:
            continue

        for pattern in profile.domain_patterns:
            if fnmatch(hostname, pattern) or fnmatch(hostname, f"www.{pattern}"):
                return profile

    return None


def save_profile(profile: SiteProfile) -> Path:
    """Save a site profile to disk."""
    profile.learned_at = datetime.now()
    filename = re.sub(r"[^a-z0-9_]", "_", profile.platform_id.lower()) + ".json"
    path = PROFILES_DIR / filename
    path.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
    return path


def update_last_used(profile: SiteProfile) -> None:
    """Update the last_used_at timestamp for a profile."""
    profile.last_used_at = datetime.now()
    save_profile(profile)


def list_profiles() -> list[SiteProfile]:
    """List all saved profiles."""
    profiles = []
    for profile_path in PROFILES_DIR.glob("*.json"):
        try:
            profiles.append(
                SiteProfile.model_validate_json(profile_path.read_text("utf-8"))
            )
        except Exception:
            continue
    return profiles


def delete_profile(platform_id: str) -> bool:
    """Delete a profile by platform_id."""
    filename = re.sub(r"[^a-z0-9_]", "_", platform_id.lower()) + ".json"
    path = PROFILES_DIR / filename
    if path.exists():
        path.unlink()
        return True
    return False
