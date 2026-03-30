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

# Manual corrections for locations that BAN/Nominatim geocode incorrectly.
# BAN often matches race names to street names (e.g. "marathon" -> "Rue de Marathon").
# Keys are lowercase. Values are (lat, lng) or None (= ungeocodable).
OVERRIDES: dict[str, tuple[float, float] | None] = {
    # Marathon de Nantes — BAN returns "Rue de Marathon" in Rennes
    "abalone marathon de nantes 2023": (47.24, -1.56),
    "abalone marathon de nantes 2024": (47.24, -1.56),
    "abalone marathon de nantes 2025": (47.24, -1.56),
    "nantes": (47.24, -1.56),
    # Marathon de la Mer — Boulogne-sur-Mer, not Bordeaux/Rennes
    "marathon de la mer - ed.2026": (50.73, 1.61),
    "marathon de la mer": (50.73, 1.61),
    # Route du Louvre — Lens (Pas-de-Calais), not a road in Sarthe
    "la route du louvre": (50.44, 2.82),
    "route du louvre": (50.44, 2.82),
    "la route du louvre - dimanche 10 mai 2026": (50.44, 2.82),
    # ENDORUN PARIS — Paris, not "Rue de Paris" in Brest
    "endorun paris 2025": (48.85, 2.41),
    "endorun paris": (48.85, 2.41),
    # Grands Trails d'Auvergne — Aubusson-d'Auvergne, not Saint-Nazaire
    "grands trails d'auvergne 2026": (45.75, 3.60),
    # Course des Crêtes — Espelette (Pays Basque), not Corsica
    "la course des cretes 2026": (43.34, -1.45),
    "course des cretes": (43.34, -1.45),
    # BEERUN — Joué-sur-Erdre (Loire-Atlantique), not Corsica
    "beerun 2025": (47.51, -1.43),
    "beerun": (47.51, -1.43),
    # 10 Miles des Baines — Capbreton (Landes), not the Alps
    "10 miles des baines 2026": (43.64, -1.42),
    # Luchon Aneto Trail — Pyrénées, not Corsica
    "luchon aneto trail 2025": (42.79, 0.59),
    "luchon aneto": (42.79, 0.59),
    # Choc des Guerriers — L'Isle-d'Espagnac (Charente), not Burgundy
    "choc des guerriers 2025": (45.66, 0.20),
    # Course des Pères Noël — Saint-Benoît 86 (near Poitiers)
    "st benoit": (46.55, 0.35),
    "la course des pères noel de st benoit le 20 décembre 2025": (46.55, 0.35),
    # Marathon Poitiers-Futuroscope — Futuroscope (Chasseneuil-du-Poitou), not Fleuré
    "poitiers-futuroscope": (46.66, 0.37),
    "marathon poitiers-futuroscope 2026": (46.66, 0.37),
    "marathon poitiers-futuroscope 2025": (46.66, 0.37),
    # Half on the Head — Ireland, not in France
    "half on the head 2026": None,
    "on the head": None,
}


def _load_cache() -> dict:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def geocode(location: str) -> tuple[float, float] | None:
    """Geocode a location string, using cache when available.

    Priority: OVERRIDES > cache > BAN API > Nominatim.
    Returns (lat, lng) or None if geocoding fails.
    """
    key = location.strip().lower()

    # Manual overrides take priority (fixes for BAN/Nominatim errors)
    if key in OVERRIDES:
        return OVERRIDES[key]

    cache = _load_cache()

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
