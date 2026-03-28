"""Scraper for Njuko platform (REST API, no HTML parsing needed).

Njuko is an Angular SPA. Registration data is fetched from a public REST API:
1. GET /edition/url/{slug} -> edition data with _id and competitions[]
2. GET /registrations/{editionId}/_search/{} -> all registrations as JSON

Event discovery: no public directory exists. We use Wayback Machine CDX index
to find all known event slugs on in.njuko.com.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

from .base import BaseScraper, Member, RaceResult, matches_club, matches_known_member


class NjukoScraper(BaseScraper):
    """Scrape registered participants from Njuko via their public API."""

    API_BASE = "https://front-api.njuko.com"
    # Njuko's API returns 403 Forbidden when the default python-requests
    # User-Agent is used.  Sending a browser-like UA avoids the block.
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
    }

    def scrape(self, race_config: dict) -> RaceResult | None:
        url = race_config.get("url", "")
        name = race_config.get("name", "Course inconnue")
        date = race_config.get("date", "")
        location = race_config.get("location", "")

        slug = self._extract_slug(url)
        if not slug:
            print(f"  [njuko] Impossible d'extraire le slug depuis {url}")
            return None

        # Step 1: Resolve edition
        edition = self._get_edition(slug)
        if not edition:
            return None

        edition_id = edition.get("_id", "")
        if not edition_id:
            print(f"  [njuko] Pas d'ID d'edition trouve")
            return None

        # Build competition lookup (id -> name)
        competitions = {}
        for comp in edition.get("competitions", []):
            comp_id = comp.get("_id", "")
            comp_name = comp.get("name", comp.get("label", ""))
            # name can be a translated array: [{"language":"fr","translation":"Trail 45km"}]
            if isinstance(comp_name, list) and comp_name:
                comp_name = comp_name[0].get("translation", comp_name[0].get("value", str(comp_name[0])))
            if comp_id:
                competitions[comp_id] = comp_name

        # Step 2: Fetch all registrations
        registrations = self._get_registrations(edition_id)
        if registrations is None:
            return None

        # Step 3: Filter for club members
        members = self._find_members(registrations, competitions)

        # Extract location from edition data if not in config
        if not location:
            addr = edition.get("address", {})
            if isinstance(addr, dict):
                city = addr.get("city", "")
                country = addr.get("country", "")
                if city:
                    location = city
                elif country and country != "FR":
                    location = country
            # Fallback: try event object
            if not location:
                event_obj = edition.get("event", {})
                if isinstance(event_obj, dict):
                    addr2 = event_obj.get("address", {})
                    if isinstance(addr2, dict):
                        location = addr2.get("city", "")

        # Extract name from edition if not in config
        if not name:
            name_arr = edition.get("name", [])
            if isinstance(name_arr, list) and name_arr:
                name = name_arr[0].get("translation", name_arr[0].get("value", "")) if isinstance(name_arr[0], dict) else str(name_arr[0])
            if not name:
                name = edition.get("reportName", slug)

        return RaceResult(
            id=f"njuko-{slug}",
            name=name,
            date=date,
            location=location,
            platform="njuko",
            url=url,
            members=members,
            member_count=len(members),
            last_scraped=datetime.now(timezone.utc).isoformat(),
        )

    def _extract_slug(self, url: str) -> str | None:
        """Extract event slug from Njuko URL.

        Supports:
        - https://in.njuko.com/{slug}
        - https://in.njuko.com/{slug}?currentPage=check-registration
        - https://www.njuko.net/{slug}/registrations-list
        - https://www.njuko.net/{slug}/check-registration
        """
        url = url.rstrip("/")
        # Remove query params
        url_path = url.split("?")[0]

        # in.njuko.com/slug
        if "njuko.com/" in url_path:
            parts = url_path.split("njuko.com/")
            if len(parts) > 1:
                return parts[1].split("/")[0]

        # njuko.net/slug/...
        if "njuko.net/" in url_path:
            parts = url_path.split("njuko.net/")
            if len(parts) > 1:
                return parts[1].split("/")[0]

        return None

    def _get_edition(self, slug: str) -> dict | None:
        """Fetch edition data from the API."""
        try:
            resp = requests.get(
                f"{self.API_BASE}/edition/url/{slug}",
                headers=self.HEADERS,
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            print(f"  [njuko] Erreur API edition '{slug}': {e}")
            return None

    def _get_registrations(self, edition_id: str) -> list | None:
        """Fetch all registrations for an edition."""
        try:
            resp = requests.get(
                f"{self.API_BASE}/registrations/{edition_id}/_search/{{}}",
                headers=self.HEADERS,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                return data
            return data.get("registrations", data.get("results", []))
        except requests.RequestException as e:
            print(f"  [njuko] Erreur API registrations: {e}")
            return None

    def _find_members(self, registrations: list, competitions: dict) -> list[Member]:
        """Find club members in the registrations list.

        Club info is in metaData array with key STRNOM_CLU or STRNOMABR_CLU.
        """
        members = []
        seen = set()

        for reg in registrations:
            if reg.get("status") not in ("COMPLETED", "VALIDATED", None):
                continue

            # Extract club from metaData
            club_name = ""
            meta = reg.get("metaData", [])
            if isinstance(meta, list):
                for item in meta:
                    if isinstance(item, dict):
                        key = item.get("key", item.get("name", ""))
                        if key in ("STRNOM_CLU", "STRNOMABR_CLU", "club"):
                            val = item.get("value", "")
                            if val and len(val) > len(club_name):
                                club_name = val
            elif isinstance(meta, dict):
                club_name = meta.get("STRNOM_CLU", meta.get("club", ""))

            firstname = reg.get("firstname", "")
            lastname = reg.get("lastname", "")
            name = f"{firstname} {lastname}".strip()

            if not name or name in seen:
                continue

            # Match by club name OR by known member name
            is_club = club_name and matches_club(club_name, self.patterns)
            is_name = matches_known_member(name, self.known_members)

            if not is_club and not is_name:
                continue
            seen.add(name)

            # Get competition/race name for bib
            comp_id = reg.get("competition", "")
            bib = competitions.get(comp_id, "")

            members.append(Member(name=name, bib=bib))

        return members


# --- Event discovery via persistent slug cache + API validation ---

NJUKO_API = "https://front-api.njuko.com"
BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_SLUG_CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "njuko_slugs.json"

# Slugs that are not events
_SLUG_BLACKLIST = {
    "check-registration", "registrations-list", "registration",
    "assets", "styles", "scripts", "favicon", "robots.txt",
    "sitemap.xml", "undefined", "null", "api", "admin", "",
}


def discover_races() -> list[dict]:
    """Discover Njuko events from a persistent slug cache + CDX seeding.

    Slug cache grows over time:
    - Seeded initially from Wayback Machine CDX (slow, best-effort)
    - New slugs found by other means can be added to data/njuko_slugs.json
    Each slug is validated against the Njuko API to get current data.
    """
    slugs = _load_slug_cache()

    # Try to seed from CDX if cache is small
    if len(slugs) < 50:
        new_slugs = _fetch_slugs_from_cdx()
        if new_slugs:
            slugs.update(new_slugs)
            _save_slug_cache(slugs)

    if not slugs:
        print("  [njuko] Aucun slug connu")
        return []

    print(f"  [njuko] {len(slugs)} slug(s), validation API...")
    races = _validate_slugs_concurrent(slugs)
    print(f"  [njuko] {len(races)} course(s) valides")
    return races


def _load_slug_cache() -> set[str]:
    if _SLUG_CACHE_PATH.exists():
        try:
            data = json.loads(_SLUG_CACHE_PATH.read_text(encoding="utf-8"))
            return set(data.get("slugs", []))
        except Exception:
            pass
    return set()


def _save_slug_cache(slugs: set[str]) -> None:
    _SLUG_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _SLUG_CACHE_PATH.write_text(
        json.dumps({"slugs": sorted(slugs)}, indent=2),
        encoding="utf-8",
    )


def _fetch_slugs_from_cdx() -> set[str]:
    """Query Wayback Machine CDX for known Njuko URLs (best-effort, slow)."""
    slugs = set()
    for domain in ("in.njuko.com", "www.njuko.net"):
        try:
            resp = requests.get(
                "https://web.archive.org/cdx/search/cdx",
                params={
                    "url": f"{domain}/*",
                    "output": "json",
                    "fl": "original",
                    "collapse": "urlkey",
                    "limit": "5000",
                },
                timeout=60,
            )
            resp.raise_for_status()
            rows = resp.json()
            for row in rows[1:]:
                url = row[0] if isinstance(row, list) else row
                slug = _extract_slug(url)
                if slug:
                    slugs.add(slug)
        except Exception:
            continue
    return slugs


def _extract_slug(url: str) -> str | None:
    match = re.search(r"njuko\.(?:com|net)/([a-zA-Z0-9_-]+)", url)
    if not match:
        return None
    slug = match.group(1).lower()
    if slug in _SLUG_BLACKLIST or len(slug) < 4:
        return None
    return slug


def _validate_slugs_concurrent(slugs: set[str]) -> list[dict]:
    """Validate slugs against the Njuko API concurrently."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    races = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(_validate_slug, s): s for s in slugs}
        for future in as_completed(futures):
            try:
                race = future.result()
                if race:
                    races.append(race)
            except Exception:
                pass
    return races


def _validate_slug(slug: str) -> dict | None:
    """Check if a slug corresponds to a valid, current Njuko edition."""
    try:
        resp = requests.get(
            f"{NJUKO_API}/edition/url/{slug}",
            headers={"User-Agent": BROWSER_UA},
            timeout=8,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
    except Exception:
        return None

    status = data.get("status", "")
    if status not in ("OPEN", "CLOSED", "FULL"):
        return None

    edition_id = data.get("_id", "")
    name_arr = data.get("name", [])
    name = ""
    if isinstance(name_arr, list) and name_arr:
        name = name_arr[0].get("value", "") if isinstance(name_arr[0], dict) else str(name_arr[0])
    if not name:
        name = data.get("reportName", slug)

    start_date = data.get("startDate", "")
    date_str = ""
    if start_date:
        dm = re.match(r"(\d{4}-\d{2}-\d{2})", start_date)
        if dm:
            date_str = dm.group(1)

    location = ""
    address = data.get("address", {})
    if isinstance(address, dict):
        city = address.get("city", "")
        if city:
            location = city

    return {
        "platform": "njuko",
        "url": f"https://in.njuko.com/{slug}",
        "name": name,
        "date": date_str,
        "location": location,
        "source": "njuko-discovery",
        "_edition_id": edition_id,
    }
