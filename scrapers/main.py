"""Orchestrator: load config, discover races, run scrapers, geocode, output JSON.

Philosophy:
- Find ALL registrations of club members across ALL platforms
- Cache responses to avoid flooding APIs during development
- Cron runs nightly — performance is not critical, completeness is
- Be respectful: cache aggressively, moderate concurrency
"""

import json
import re
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yaml

from .base import Member, RaceResult
from .geocoder import geocode
from .chronometrage import ChronometrageScraper
from .chronometrage import discover_races as chronometrage_discover
from .chronostart import ChronoStartScraper
from .chronostart import discover_races as chronostart_discover
from .endurancechrono import EnduranceChronoScraper
from .endurancechrono import discover_races as endurancechrono_discover
from .espacecompetition import EspaceCompetitionScraper
from .espacecompetition import discover_races as espacecomp_discover
from .listino import ListinoScraper
from .listino import discover_races as listino_discover
from .klikego import KlikegoScraper
from .klikego import discover_races as klikego_discover
from .njuko import NjukoScraper
from .njuko import discover_races as njuko_discover
from .onsinscrit import OnSinscritScraper
from .onsinscrit import discover_races as onsinscrit_discover
from .protiming import ProtimingScraper
from .protiming import discover_races as protiming_discover
from .sportips import SportipsScraper
from .sportips import discover_races as sportips_discover
from .threewsport import ThreeWSportScraper
from .threewsport import discover_races as threewsport_discover
from .timepulse import TimePulseScraper
from .timepulse import discover_races as timepulse_discover
from .runchrono import discover_races as runchrono_discover

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yml"
DATA_PATH = ROOT / "data" / "races.json"
DOCS_DATA_PATH = ROOT / "docs" / "data" / "races.json"
SCRAPE_CACHE_PATH = ROOT / "data" / "scrape_cache.json"

SCRAPERS = {
    "klikego": KlikegoScraper,
    "njuko": NjukoScraper,
    "onsinscrit": OnSinscritScraper,
    "protiming": ProtimingScraper,
    "chronometrage": ChronometrageScraper,
    "chronostart": ChronoStartScraper,
    "3wsport": ThreeWSportScraper,
    "espace-competition": EspaceCompetitionScraper,
    "sportips": SportipsScraper,
    "timepulse": TimePulseScraper,
    "endurancechrono": EnduranceChronoScraper,
    "listino": ListinoScraper,
}

# Patterns for extracting location from race names
_PREFIX_RE = re.compile(
    r"^(marathon|semi[- ]?marathon|trail|course|foul[ée]es?|cross|grand|"
    r"corrida|rando[- ]?trail|boucles?|nocturne|run|festival|"
    r"harmonie\s+mutuelle|schneider\s+electric|"
    r"p[èe]res?\s*no[eë]ls?|half|"
    r"les?|la|du|de|des|l'|d')\s+",
    re.IGNORECASE,
)
_SUFFIX_RE = re.compile(
    r"\s+(trail|run\s*festival|run|festival|marathon|nocturne|"
    r"course|cross|corrida|[çc]a\s+bouge)$",
    re.IGNORECASE,
)


def _extract_location_from_name(name: str) -> list[str]:
    """Try to extract geocodable locations from a race name.

    Returns a list of candidate queries, best first.
    Strategies (tried in order):
    1. Strip event-type prefixes/suffixes, sponsors, distances, year
    2. Extract text after last "de/d'/à" preposition (location indicator)

    E.g. "Marathon Poitiers-Futuroscope 2026" -> ["Poitiers-Futuroscope"]
         "La Course des Pères Noel de St Benoit le 20 décembre 2025"
           -> ["Pères Noel St Benoit", "St Benoit"]
    """
    candidates = []

    # --- Strategy 1: strip prefixes/suffixes (more complete) ---
    cleaned = name
    # Strip trailing year
    cleaned = re.sub(r"\s+\d{4}$", "", cleaned).strip()
    # Strip trailing date like "le 20 décembre"
    cleaned = re.sub(
        r"\s+le\s+\d{1,2}\s+\w+$", "", cleaned, flags=re.IGNORECASE
    ).strip()
    # Strip distances like "10K", "10-20K", "42.195km"
    cleaned = re.sub(r"\s+\d+[\-,.]?\d*\s*[kK][mM]?\b", "", cleaned).strip()
    # Strip leading prefixes repeatedly
    while _PREFIX_RE.search(cleaned):
        cleaned = _PREFIX_RE.sub("", cleaned, count=1).strip()
    # Strip trailing suffixes
    cleaned = _SUFFIX_RE.sub("", cleaned).strip()
    if cleaned and cleaned.lower() != name.lower():
        candidates.append(cleaned)

    # --- Strategy 2: extract location after last "de/d'/à" preposition ---
    stripped = re.sub(r"\s+\d{4}$", "", name).strip()
    loc_matches = list(re.finditer(
        r"\b(?:de|d'|à)\s+([A-ZÀ-Ÿ][a-zà-ÿ''-]+(?:[\s-]+[A-ZÀ-Ÿa-zà-ÿ''-]+)*)",
        stripped,
    ))
    if loc_matches:
        after_de = loc_matches[-1].group(1).strip()
        # Clean trailing date patterns like "le 20 décembre"
        after_de = re.sub(
            r"\s+le\s+\d{1,2}(\s+\w+)?$", "", after_de, flags=re.IGNORECASE
        ).strip()
        # Strip trailing articles (le/la/les/sur)
        after_de = re.sub(
            r"\s+(le|la|les|sur)$", "", after_de, flags=re.IGNORECASE
        ).strip()
        if after_de and len(after_de) > 2:
            if not candidates or after_de.lower() != candidates[0].lower():
                candidates.append(after_de)

    return candidates



MAX_WORKERS = 6

# Cache TTL: avoid re-scraping the same URL too often
CACHE_TTL_EMPTY = 48       # 2 days for courses with 0 members
CACHE_TTL_WITH_MEMBERS = 6  # 6h for courses with members (check for new registrations)


# --- Config & data ---

def load_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def save_data(data: dict) -> None:
    # Full version with member names (local only, gitignored)
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # Anonymized version for GitHub Pages (no personal data)
    public_data = {
        "last_updated": data["last_updated"],
        "races": [
            {k: v for k, v in race.items() if k != "members"}
            for race in data.get("races", [])
        ],
    }
    DOCS_DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DOCS_DATA_PATH.write_text(
        json.dumps(public_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# --- Scrape cache ---

def load_scrape_cache() -> dict:
    if SCRAPE_CACHE_PATH.exists():
        try:
            return json.loads(SCRAPE_CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_scrape_cache(cache: dict) -> None:
    SCRAPE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCRAPE_CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def should_scrape(url: str, cache: dict) -> bool:
    entry = cache.get(url)
    if not entry:
        return True
    last = entry.get("last_scraped", "")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
    except (ValueError, TypeError):
        return True
    member_count = entry.get("member_count", 0)
    ttl = CACHE_TTL_WITH_MEMBERS if member_count > 0 else CACHE_TTL_EMPTY
    return datetime.now(timezone.utc) - last_dt > timedelta(hours=ttl)


# --- Scraping ---

def process_manual_race(race_config: dict) -> RaceResult:
    members_raw = race_config.get("members", [])
    members = []
    for m in members_raw:
        if isinstance(m, dict):
            members.append(Member(name=m.get("name", ""), bib=m.get("bib", "")))
        elif isinstance(m, str):
            members.append(Member(name=m))
    name = race_config.get("name", "Course manuelle")
    date = race_config.get("date", "")
    return RaceResult(
        id=f"manual-{name.lower().replace(' ', '-')}-{date}",
        name=name, date=date,
        location=race_config.get("location", ""),
        platform="manual", members=members,
        member_count=len(members),
        last_scraped=datetime.now(timezone.utc).isoformat(),
    )


def scrape_race(rc: dict, patterns: list[str], known_members: list[str]) -> dict | None:
    """Scrape a single race. Returns data dict or None."""
    platform = rc.get("platform", "")

    if platform == "manual":
        race = process_manual_race(rc)
    elif platform in SCRAPERS:
        cls = SCRAPERS[platform]
        scraper = cls(patterns, known_members=known_members)
        try:
            race = scraper.scrape(rc)
        except Exception:
            return None
    else:
        return None

    if race:
        return asdict(race)
    return None


def run():
    config = load_config()
    club = config.get("club", {})
    patterns = club.get("patterns", [])
    known_members = club.get("known_members", [])
    races_config = list(config.get("races") or [])
    scrape_cache = load_scrape_cache()

    # --- Auto-discovery (all platforms, national) ---
    discoveries = [
        ("Klikego", klikego_discover),
        ("Protiming", protiming_discover),
        ("OnSinscrit", onsinscrit_discover),
        ("Njuko", njuko_discover),
        ("Chronometrage.com", chronometrage_discover),
        ("Chrono-Start", chronostart_discover),
        ("3wsport", threewsport_discover),
        ("Espace-Competition", espacecomp_discover),
        ("Sportips", sportips_discover),
        ("TimePulse", timepulse_discover),
        ("Endurance Chrono", endurancechrono_discover),
        ("Listino", listino_discover),
        ("RunChrono (local 86)", runchrono_discover),
    ]

    discovered = []
    for label, discover_fn in discoveries:
        print(f"=== Auto-decouverte {label} ===")
        try:
            found = discover_fn()
            discovered.extend(found)
        except Exception as e:
            print(f"  Erreur: {e}")

    # Merge: config takes priority, then discovered (deduplicated by URL)
    config_urls = {rc.get("url", "").rstrip("/") for rc in races_config}
    for disc in discovered:
        disc_url = disc.get("url", "").rstrip("/")
        if disc_url and disc_url not in config_urls:
            races_config.append(disc)
            config_urls.add(disc_url)

    # --- Split: cached vs to-scrape ---
    to_scrape = []
    cached_results = []
    for rc in races_config:
        url = rc.get("url", "")
        if should_scrape(url, scrape_cache):
            to_scrape.append(rc)
        else:
            entry = scrape_cache.get(url, {})
            if entry.get("member_count", 0) > 0 and entry.get("data"):
                cached_results.append(entry["data"])

    total = len(races_config)
    cached = total - len(to_scrape)
    print(f"\n=== {total} courses, {cached} en cache, {len(to_scrape)} a scraper ===")

    # --- Scrape concurrently ---
    results: list[dict] = list(cached_results)
    found_count = len(cached_results)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_rc = {
            executor.submit(scrape_race, rc, patterns, known_members): rc
            for rc in to_scrape
        }
        done = 0
        for future in as_completed(future_to_rc):
            done += 1
            rc = future_to_rc[future]
            platform = rc.get("platform", "")
            name = rc.get("name", "?")
            url = rc.get("url", "")

            try:
                data = future.result()
            except Exception:
                data = None

            member_count = data.get("member_count", 0) if data else 0
            # Store data without lat/lng — geocoding is done separately
            # and coords must come from the (correctable) geocache, not
            # from a stale scrape cache entry.
            cache_data = None
            if data and member_count > 0:
                cache_data = {k: v for k, v in data.items() if k not in ("lat", "lng")}
            scrape_cache[url] = {
                "last_scraped": datetime.now(timezone.utc).isoformat(),
                "member_count": member_count,
                "data": cache_data,
            }

            if data and member_count > 0:
                results.append(data)
                found_count += 1
                print(f"  [{platform}] {name} -> {member_count} membre(s) !")

            if done % 100 == 0:
                print(f"  ... {done}/{len(to_scrape)} ({found_count} avec membres)")

    print(f"  ... {done}/{len(to_scrape)} termine")
    save_scrape_cache(scrape_cache)

    # --- Geocode (BAN API is fast, Nominatim fallback for international) ---
    # Always re-geocode via the geocache (which has manual corrections).
    # Never blindly copy coords from previous runs or scrape cache,
    # as those may contain wrong values from bad BAN/Nominatim results.
    for race in results:
        if race.get("lat") is not None and race.get("lng") is not None:
            # Already has coords (from scraper or cache). Keep if location
            # was provided by the platform (trustworthy).
            if race.get("location", "").strip():
                continue
            # No location field — coords came from name-based geocoding.
            # Strip them so we re-geocode using the (possibly corrected) cache.
            race.pop("lat", None)
            race.pop("lng", None)

        # Build geocoding queries: location field, race name, cleaned name
        queries = [race.get("location", ""), race.get("name", "")]
        name = race.get("name", "")
        if name:
            candidates = _extract_location_from_name(name)
            for c in candidates:
                if c.lower() != name.lower() and c not in queries:
                    queries.append(c)
        for query in queries:
            if not query:
                continue
            print(f"  Geocoding '{query}'...")
            coords = geocode(query)
            if coords:
                race["lat"], race["lng"] = coords
                break

    # --- Save ---
    output = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "races": results,
    }
    save_data(output)
    print(f"\n=== {len(results)} course(s) avec membres / {total} decouvertes ===")


if __name__ == "__main__":
    run()
