"""Orchestrator: load config, discover races, run scrapers, geocode, output JSON.

Philosophy:
- Find ALL registrations of club members across ALL platforms
- Cache responses to avoid flooding APIs during development
- Cron runs nightly — performance is not critical, completeness is
- Be respectful: cache aggressively, moderate concurrency
"""

import json
import os
import re
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yaml

from .base import Member, RaceResult, normalize_text, matches_known_member
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
from .njuko import discover_utmb_races as utmb_discover
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
from .ipitos import IpitosScraper
from .ipitos import discover_races as ipitos_discover
from .runchrono import discover_races as runchrono_discover
from .sportsnconnect import SportsnConnectScraper
from .sportsnconnect import discover_races as sportsnconnect_discover
from .adeorun import AdeorunScraper
from .adeorun import discover_races as adeorun_discover

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yml"
DATA_PATH = ROOT / "data" / "races.json"
DOCS_DATA_PATH = ROOT / "docs" / "data" / "races.json"
# Public iCalendar feed (subscribe via webcal://) of upcoming races with members.
ICS_PATH = ROOT / "docs" / "data" / "races.ics"
SCRAPE_CACHE_PATH = ROOT / "data" / "scrape_cache.json"
# Persistent archive: accumulates every race ever found with members, so past
# races stay on the map after their participant list goes offline.
ARCHIVE_PATH = ROOT / "data" / "races_archive.json"

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
    "ipitos": IpitosScraper,
    "sportsnconnect": SportsnConnectScraper,
    "adeorun": AdeorunScraper,
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



def _extract_first_name(member_name: str, known_members: list[str]) -> str:
    """Extract first name from a full name.

    Strategy:
    1. Match against known_members (format "NOM Prenom") to get reliable first name
    2. Fallback: in French race data, uppercase parts = last name, mixed-case = first name
    3. If all uppercase (e.g. "DUPONT JULIEN"), assume NOM PRENOM order, title-case the rest
    """
    # Try matching against known_members for reliable extraction
    name_parts = set(normalize_text(member_name).lower().split())
    if len(name_parts) >= 2:
        for km in known_members:
            km_parts_raw = km.split()
            km_parts_norm = set(normalize_text(km).lower().split())
            if len(km_parts_norm) < 2:
                continue
            shorter, longer = (
                (name_parts, km_parts_norm)
                if len(name_parts) <= len(km_parts_norm)
                else (km_parts_norm, name_parts)
            )
            if shorter.issubset(longer):
                # Found match — extract first name from config entry
                first_parts = [p for p in km_parts_raw if not p.isupper()]
                if first_parts:
                    return " ".join(first_parts)

    # Fallback: heuristic on the scraped name
    parts = member_name.split()
    first_parts = [p for p in parts if not p.isupper()]
    if first_parts:
        return " ".join(first_parts)

    # All uppercase: assume NOM PRENOM, take everything except first word
    if len(parts) >= 2:
        return " ".join(p.title() for p in parts[1:])
    return parts[0].title() if parts else ""


def _is_opted_in(member_name: str, display_optin: list[str]) -> bool:
    """Check if a member has opted in for first name display."""
    if not display_optin:
        return False
    return matches_known_member(member_name, display_optin)


def _build_display_names(optin: list[str], known_members: list[str]) -> dict[str, str]:
    """Build display names for opted-in members, disambiguating duplicate first names.

    E.g. if "FAITEAU Romain" and "RICHARD Romain" are both opted in,
    returns {"FAITEAU Romain": "Romain F.", "RICHARD Romain": "Romain R."}.
    """
    from collections import Counter

    # Extract raw first name for each optin entry
    raw = {entry: _extract_first_name(entry, known_members) for entry in optin}

    # Detect duplicates
    counts = Counter(raw.values())

    # Disambiguate with last name initial where needed
    display = {}
    for entry, first in raw.items():
        if counts[first] > 1:
            # First uppercase word in config entry = last name
            last_initial = entry.split()[0][0]
            display[entry] = f"{first} {last_initial}."
        else:
            display[entry] = first
    return display


MAX_WORKERS = 6

# Hard budget for the whole scraping phase. A single misbehaving site must never
# hang the run: once this is exceeded, stragglers are abandoned and the pipeline
# continues to geocoding/save with whatever was found. Overridable via env for
# cold/recovery runs (empty cache scrapes all ~3000 events). Kept under the job's
# timeout-minutes: 50. Normal cached runs finish well under this.
SCRAPE_BUDGET_S = int(os.environ.get("SCRAPE_BUDGET_S", 40 * 60))  # default 40 min

# Cache TTL: avoid re-scraping the same URL too often
CACHE_TTL_EMPTY = 48       # 2 days for courses with 0 members
CACHE_TTL_WITH_MEMBERS = 6  # 6h for courses with members (check for new registrations)


# --- Config & data ---

def load_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def _detect_race_type(name: str, bibs: list[str]) -> str:
    """Detect race type from name and bib fields: trail, route, or other."""
    combined = (name + " " + " ".join(bibs)).lower()
    if re.search(r"\btrail\b", combined):
        return "trail"
    if re.search(
        r"\b(marathon|semi|foul[ée]es?|corrida|\d+\s*km|\d+\s*miles|course|run\b|relais|endorun)",
        combined,
    ):
        return "route"
    if re.search(r"\b(marche|rando)", combined):
        return "marche"
    return "autre"


def _extract_distances(bibs: list[str]) -> list[float]:
    """Extract distances (in km) from bib/race names."""
    distances = set()
    for b in bibs:
        # Match "10KM", "21.1km", "27K", "45km", "80km"
        for m in re.finditer(r"(\d+(?:[.,]\d+)?)\s*(?:km|k)\b", b, re.IGNORECASE):
            distances.add(round(float(m.group(1).replace(",", ".")), 1))
        # "Marathon" -> 42km, "Semi-Marathon" -> 21km
        if re.search(r"\bmarathon\b", b, re.IGNORECASE) and not re.search(
            r"\bsemi", b, re.IGNORECASE
        ):
            distances.add(42.0)
        elif re.search(r"\bsemi", b, re.IGNORECASE):
            distances.add(21.0)
    return sorted(distances)


def _enrich_race(race: dict) -> dict:
    """Add race_type and distances fields to a race dict."""
    bibs = [m.get("bib", "") for m in race.get("members", []) if m.get("bib")]
    # Use platform-provided race_type if already set, else detect from text
    if not race.get("race_type"):
        race["race_type"] = _detect_race_type(race.get("name", ""), bibs)
    # Merge platform-provided distances with bib-extracted ones
    existing = set(race.get("distances") or [])
    existing.update(_extract_distances(bibs))
    race["distances"] = sorted(existing)
    return race


def load_archive() -> dict:
    """Load the persistent race archive, keyed by race id."""
    if ARCHIVE_PATH.exists():
        try:
            data = json.loads(ARCHIVE_PATH.read_text(encoding="utf-8"))
            return {r["id"]: r for r in data.get("races", []) if r.get("id")}
        except Exception:
            pass
    return {}


def merge_archive(results: list[dict]) -> tuple[list[dict], list[dict]]:
    """Merge this run's races into the persistent archive.

    Past races no longer surfaced by discovery (participant list offline) are
    retained with their last-known members and coordinates. Races found again
    this run overwrite their archived version with fresh data.

    Returns (all_races, new_races) where new_races are ids never seen before
    (used to feed the WhatsApp notification queue).
    """
    archive = load_archive()
    known_ids = set(archive.keys())
    new_races = []
    for race in results:
        rid = race.get("id")
        if not rid:
            continue
        if rid not in known_ids:
            new_races.append(race)
        archive[rid] = race  # fresh data wins

    ARCHIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
    ARCHIVE_PATH.write_text(
        json.dumps(
            {
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "races": list(archive.values()),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return list(archive.values()), new_races


def save_data(
    data: dict,
    known_members: list[str] | None = None,
    display_optin: list[str] | None = None,
) -> None:
    # Enrich races with type and distances before saving
    for race in data.get("races", []):
        _enrich_race(race)

    # Full version with member names (local only, gitignored)
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # Public version for GitHub Pages: first names only (no last names)
    # Deployed via GitHub Actions artifact — never committed to Git.
    # Only members who explicitly opted in have their first name shown.
    km = known_members or []
    optin = display_optin or []
    display_names = _build_display_names(optin, km)
    public_races = []
    for race in data.get("races", []):
        public_race = {k: v for k, v in race.items() if k != "members"}
        # Build first_names list — only for members who consented (opt-in)
        first_names = []
        for member in race.get("members", []):
            name = member.get("name", "") if isinstance(member, dict) else member.name
            if not name:
                continue
            if not _is_opted_in(name, optin):
                continue
            # Find which optin entry this member matches for display name
            for entry in optin:
                if matches_known_member(name, [entry]):
                    first_names.append(display_names[entry])
                    break
        public_race["first_names"] = first_names
        public_races.append(public_race)

    public_data = {
        "last_updated": data["last_updated"],
        "races": public_races,
    }
    DOCS_DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DOCS_DATA_PATH.write_text(
        json.dumps(public_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Public calendar feed (subscribe via webcal://) — upcoming races only
    generate_ics(public_races)


# --- iCalendar feed ---

def _ics_escape(text: str) -> str:
    """Escape a text value per RFC 5545 (backslash, comma, semicolon, newline)."""
    return (
        (text or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _ics_fold(line: str) -> str:
    """Fold a content line to <=75 octets with CRLF + space continuation."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    out, chunk = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        # First line caps at 75 octets, continuations at 74 (leading space)
        cap = 75 if not out else 74
        if len(chunk) + len(b) > cap:
            out.append(chunk.decode("utf-8"))
            chunk = b
        else:
            chunk += b
    out.append(chunk.decode("utf-8"))
    return "\r\n ".join(out)


def generate_ics(public_races: list[dict]) -> None:
    """Write docs/data/races.ics: one all-day VEVENT per upcoming race.

    Uses the same public, opted-in data as the public JSON (first names only).
    Designed to be subscribed to via webcal:// so phones refresh it automatically.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//RunEvent86//Ou court le club//FR",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Courses Run Event 86",
        "NAME:Courses Run Event 86",
        "X-WR-TIMEZONE:Europe/Paris",
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
        "X-PUBLISHED-TTL:PT12H",
    ]

    count = 0
    for race in public_races:
        date = (race.get("date") or "")[:10]
        if not date or date < today:
            continue
        try:
            d = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            continue

        dtstart = d.strftime("%Y%m%d")
        dtend = (d + timedelta(days=1)).strftime("%Y%m%d")
        name = race.get("name", "Course")
        mc = race.get("member_count", 0)
        names = race.get("first_names") or []
        location = race.get("location") or ""
        url = race.get("url") or ""
        uid = race.get("id") or f"{dtstart}-{name}"

        summary = f"🏃 {name} ({mc} RunEvent)"
        first_line = f"{mc} membre(s) du club inscrit(s)"
        if names:
            first_line += " : " + ", ".join(names)
        desc = first_line + (f"\n{url}" if url else "")

        ev = [
            "BEGIN:VEVENT",
            f"UID:{_ics_escape(uid)}@runevent86",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{dtstart}",
            f"DTEND;VALUE=DATE:{dtend}",
            f"SUMMARY:{_ics_escape(summary)}",
            f"DESCRIPTION:{_ics_escape(desc)}",
        ]
        if location:
            ev.append(f"LOCATION:{_ics_escape(location)}")
        if url:
            ev.append(f"URL:{url}")
        ev.append("END:VEVENT")
        lines.extend(ev)
        count += 1

    lines.append("END:VCALENDAR")

    ICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    ICS_PATH.write_text("\r\n".join(_ics_fold(ln) for ln in lines) + "\r\n", encoding="utf-8")
    print(f"  Calendrier .ics: {count} course(s) à venir → {ICS_PATH.name}")


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
        data = asdict(race)
        # Forward structured metadata from discovery (e.g. race_type from
        # chronometrage.com tourism_category) so _enrich_race can use it.
        if rc.get("race_type"):
            data["race_type"] = rc["race_type"]
        if rc.get("distances"):
            data.setdefault("distances", [])
            existing = set(data["distances"])
            for d in rc["distances"]:
                if d not in existing:
                    data["distances"].append(d)
                    existing.add(d)
        return data
    return None


def _elapsed(start: float) -> str:
    """Format elapsed time since start."""
    import time
    secs = time.time() - start
    if secs < 60:
        return f"{secs:.0f}s"
    return f"{int(secs // 60)}m{int(secs % 60):02d}s"


def run():
    import time
    run_start = time.time()

    config = load_config()
    club = config.get("club", {})
    patterns = club.get("patterns", [])
    known_members = club.get("known_members", [])
    races_config = list(config.get("races") or [])
    scrape_cache = load_scrape_cache()

    print(f"{'='*60}")
    print(f"  RunEvent86 — Scraper Pipeline")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  {len(patterns)} club patterns, {len(known_members)} known members")
    print(f"{'='*60}")

    # --- Auto-discovery (all platforms, national) ---
    discoveries = [
        ("Klikego", klikego_discover),
        ("Protiming", protiming_discover),
        ("OnSinscrit", onsinscrit_discover),
        ("Njuko", njuko_discover),
        ("UTMB (Njuko)", utmb_discover),
        ("Chronometrage.com", chronometrage_discover),
        ("Chrono-Start", chronostart_discover),
        ("3wsport", threewsport_discover),
        ("Espace-Competition", espacecomp_discover),
        ("Sportips", sportips_discover),
        ("TimePulse", timepulse_discover),
        ("Endurance Chrono", endurancechrono_discover),
        ("Listino", listino_discover),
        ("IPITOS", ipitos_discover),
        ("RunChrono (local 86)", runchrono_discover),
        ("SportsnConnect", sportsnconnect_discover),
        ("Adeorun", adeorun_discover),
    ]

    discovered = []
    print(f"\n{'─'*60}")
    print(f"  PHASE 1/4 — Decouverte ({len(discoveries)} plateformes)")
    print(f"{'─'*60}")
    discovery_start = time.time()
    for i, (label, discover_fn) in enumerate(discoveries, 1):
        print(f"  [{i:2d}/{len(discoveries)}] {label:25s}", end="", flush=True)
        try:
            found = discover_fn()
            discovered.extend(found)
            print(f" -> {len(found):5d} courses ({_elapsed(discovery_start)})")
        except Exception as e:
            print(f" -> ERREUR: {e}")
    print(f"  Total: {len(discovered)} courses decouvertes ({_elapsed(discovery_start)})")

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
                data = entry["data"]
                # Forward structured metadata from discovery (e.g. race_type)
                if rc.get("race_type") and not data.get("race_type"):
                    data["race_type"] = rc["race_type"]
                cached_results.append(data)

    total = len(races_config)
    cached = total - len(to_scrape)
    print(f"\n{'─'*60}")
    print(f"  PHASE 2/4 — Scraping ({len(to_scrape)} courses, {cached} en cache)")
    print(f"{'─'*60}")
    scrape_start = time.time()

    # --- Scrape concurrently ---
    results: list[dict] = list(cached_results)
    found_count = len(cached_results)

    # Bounded by SCRAPE_BUDGET_S: a misbehaving site must never hang the run.
    # We don't use the context manager (its __exit__ would block on stragglers);
    # on budget overrun we abandon unfinished tasks and continue to save.
    executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
    future_to_rc = {
        executor.submit(scrape_race, rc, patterns, known_members): rc
        for rc in to_scrape
    }
    done = 0
    try:
        for future in as_completed(future_to_rc, timeout=SCRAPE_BUDGET_S):
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
                print(f"  ✓ [{platform}] {name[:45]} -> {member_count} membre(s) !")

            if done % 200 == 0:
                pct = done * 100 // len(to_scrape) if to_scrape else 100
                print(f"  ... {done}/{len(to_scrape)} ({pct}%) — {found_count} avec membres ({_elapsed(scrape_start)})")
    except FuturesTimeoutError:
        unfinished = sum(1 for f in future_to_rc if not f.done())
        print(
            f"  ⚠️ Budget scraping ({SCRAPE_BUDGET_S // 60} min) dépassé — "
            f"{unfinished} course(s) abandonnée(s), on continue avec {found_count} trouvée(s)"
        )
    finally:
        # Don't wait on stragglers; pending (not-yet-started) tasks are cancelled.
        executor.shutdown(wait=False, cancel_futures=True)

    print(f"  ... {done}/{len(to_scrape)} traitées — {found_count} avec membres ({_elapsed(scrape_start)})")
    save_scrape_cache(scrape_cache)

    # --- Geocode ---
    print(f"\n{'─'*60}")
    print(f"  PHASE 3/4 — Geocoding ({len(results)} courses avec membres)")
    print(f"{'─'*60}")
    geo_start = time.time()
    geo_count = 0
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
                geo_count += 1
                break

    no_coords = sum(1 for r in results if r.get("lat") is None)
    print(f"  {geo_count} geocodes, {no_coords} sans coordonnees ({_elapsed(geo_start)})")

    # --- Save ---
    print(f"\n{'─'*60}")
    print(f"  PHASE 4/4 — Sauvegarde & enrichissement")
    print(f"{'─'*60}")
    # Enrich fresh races before archiving so the archive stores complete data
    for race in results:
        _enrich_race(race)

    # Merge into the persistent archive: past races stay on the map, and we get
    # the list of races detected for the first time (for WhatsApp notifications).
    fresh_count = len(results)
    all_races, new_races = merge_archive(results)
    archived_count = len(all_races) - fresh_count
    print(
        f"  Archive: {fresh_count} course(s) ce run, "
        f"{archived_count} archivée(s) conservée(s), {len(new_races)} nouvelle(s)"
    )

    display_optin = club.get("display_optin", [])
    output = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "races": all_races,
    }
    save_data(output, known_members=known_members, display_optin=display_optin)

    # --- Summary ---
    from collections import Counter
    plat_counts = Counter(r.get("platform", "?") for r in results)
    type_counts = Counter(r.get("race_type", "?") for r in results)
    total_members = sum(r.get("member_count", 0) for r in results)

    print(f"\n{'='*60}")
    print(f"  TERMINE — {_elapsed(run_start)}")
    print(f"{'='*60}")
    print(f"  Courses decouvertes:  {total}")
    print(f"  Courses avec membres: {len(results)}")
    print(f"  Inscriptions club:    {total_members}")
    print(f"  Sans coordonnees:     {no_coords}")
    print(f"\n  Par plateforme:")
    for p, c in plat_counts.most_common():
        print(f"    {p:20s} {c:3d} courses")
    print(f"\n  Par type:")
    for t, c in type_counts.most_common():
        print(f"    {t:10s} {c:3d}")
    print(f"{'='*60}")


if __name__ == "__main__":
    run()
