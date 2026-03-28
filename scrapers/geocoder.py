"""Geocoding with api-adresse.data.gouv.fr (BAN) and persistent JSON cache.

The BAN (Base Adresse Nationale) API is free, fast, no API key required.
Fallback to Nominatim for international addresses.
"""

import json
import time
from pathlib import Path

import requests

CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "geocache.json"

BAN_URL = "https://api-adresse.data.gouv.fr/search"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


def _load_cache() -> dict:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def geocode(location: str) -> tuple[float, float] | None:
    """Geocode a location string, using cache when available.

    Tries BAN (French government API) first, then Nominatim for international.
    Returns (lat, lng) or None if geocoding fails.
    """
    cache = _load_cache()
    key = location.strip().lower()

    if key in cache:
        entry = cache[key]
        if entry is None:
            return None
        return entry["lat"], entry["lng"]

    # Try BAN first (fast, French addresses)
    coords = _geocode_ban(location)

    # Fallback to Nominatim (international)
    if coords is None:
        coords = _geocode_nominatim(location)

    # Cache result (even None to avoid retrying)
    if coords:
        cache[key] = {"lat": coords[0], "lng": coords[1]}
    else:
        cache[key] = None
    _save_cache(cache)

    return coords


def _geocode_ban(location: str) -> tuple[float, float] | None:
    """Geocode via the French BAN API (api-adresse.data.gouv.fr)."""
    try:
        resp = requests.get(
            BAN_URL,
            params={"q": location, "limit": 1},
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None

    features = data.get("features", [])
    if not features:
        return None

    coords = features[0].get("geometry", {}).get("coordinates", [])
    if len(coords) >= 2:
        # BAN returns [lng, lat] (GeoJSON order)
        return coords[1], coords[0]

    return None


def _geocode_nominatim(location: str) -> tuple[float, float] | None:
    """Geocode via Nominatim (for international addresses). Rate limited."""
    time.sleep(1)  # Nominatim requires 1 req/sec
    try:
        resp = requests.get(
            NOMINATIM_URL,
            params={
                "q": location,
                "format": "json",
                "limit": 1,
            },
            headers={"User-Agent": "runevent86-map"},
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json()
    except Exception:
        return None

    if results:
        return float(results[0]["lat"]), float(results[0]["lon"])

    return None
