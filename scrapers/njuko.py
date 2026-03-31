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
    # White-label domains that use a separate API base URL
    _API_BASES = {
        "sporkrono-inscriptions.fr": "https://front-api.sporkrono-inscriptions.fr",
        "sports107.com": "https://front-api.sports107.com",
        "timeto.com": "https://front-api.timeto.com",
    }
    # Njuko's API returns 403 Forbidden when the default python-requests
    # User-Agent is used.  Sending a browser-like UA avoids the block.
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
    }

    def _api_base_for_url(self, url: str) -> str:
        """Return the API base URL for a given registration page URL.

        White-label Njuko platforms (e.g. sporkrono-inscriptions.fr) use
        their own front-api subdomain instead of front-api.njuko.com.
        """
        for domain, api_base in self._API_BASES.items():
            if domain in url:
                return api_base
        return self.API_BASE

    def scrape(self, race_config: dict) -> RaceResult | None:
        url = race_config.get("url", "")
        name = race_config.get("name", "Course inconnue")
        date = race_config.get("date", "")
        location = race_config.get("location", "")

        slug = self._extract_slug(url)
        if not slug:
            print(f"  [njuko] Impossible d'extraire le slug depuis {url}")
            return None

        api_base = self._api_base_for_url(url)

        # Step 1: Resolve edition
        edition = self._get_edition(slug, api_base=api_base)
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

        # Step 2: Fetch registrations
        registrations = self._get_registrations(edition_id, api_base=api_base)

        if registrations is not None:
            # Step 3a: Filter for club members (normal path)
            members = self._find_members(registrations, competitions)
        else:
            # Step 3b: Bulk fetch failed (timeout on large events like Marathon
            # de Paris with 50k+ registrants). Fall back to per-name search.
            print(f"  [njuko] Bulk fetch failed, searching by name...")
            members = []
            seen = set()
            for full_name in (self.known_members or []):
                parts = full_name.strip().split()
                if not parts:
                    continue
                last_name = parts[0] if parts[0].isupper() else parts[-1]
                results = self._search_registrations(
                    edition_id, last_name, api_base=api_base
                )
                for reg in results:
                    found = self._find_members([reg], competitions)
                    for m in found:
                        if m.name.lower() not in seen:
                            members.append(m)
                            seen.add(m.name.lower())

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
        - https://in.register-utmb.world/{slug}
        - https://in.sporkrono-inscriptions.fr/{slug}
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

        # register-utmb.world/slug (UTMB uses Njuko API under the hood)
        if "register-utmb.world/" in url_path:
            parts = url_path.split("register-utmb.world/")
            if len(parts) > 1:
                slug = parts[1].split("/")[0]
                if slug:
                    return slug

        # Njuko white-label platforms (Sporkrono, Sports107, etc.)
        for domain in ("sporkrono-inscriptions.fr/", "sports107.com/", "timeto.com/"):
            if domain in url_path:
                parts = url_path.split(domain)
                if len(parts) > 1:
                    slug = parts[1].split("/")[0]
                    if slug:
                        return slug

        return None

    def _get_edition(self, slug: str, *, api_base: str | None = None) -> dict | None:
        """Fetch edition data from the API."""
        base = api_base or self.API_BASE
        try:
            resp = requests.get(
                f"{base}/edition/url/{slug}",
                headers=self.HEADERS,
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            print(f"  [njuko] Erreur API edition '{slug}': {e}")
            return None

    def _get_registrations(self, edition_id: str, *, api_base: str | None = None) -> list | None:
        """Fetch all registrations for an edition."""
        base = api_base or self.API_BASE
        try:
            resp = requests.get(
                f"{base}/registrations/{edition_id}/_search/{{}}",
                headers=self.HEADERS,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                return data
            return data.get("registrations", data.get("results", []))
        except requests.RequestException as e:
            print(f"  [njuko] Erreur API registrations (bulk): {e}")
            return None

    def _search_registrations(self, edition_id: str, search_term: str,
                              *, api_base: str | None = None) -> list:
        """Search registrations by name (for large events where bulk fetch times out)."""
        base = api_base or self.API_BASE
        import json as _json
        search_body = _json.dumps({"search": search_term})
        try:
            resp = requests.get(
                f"{base}/registrations/{edition_id}/_search/{search_body}",
                headers=self.HEADERS,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                return data
            return data.get("registrations", data.get("results", []))
        except requests.RequestException:
            return []

    def _find_members(self, registrations: list, competitions: dict) -> list[Member]:
        """Find club members in the registrations list.

        Club info is in metaData array with key STRNOM_CLU or STRNOMABR_CLU.
        """
        members = []
        seen = set()

        for reg in registrations:
            # Accept confirmed registrations. "IN PROGRESS" on UTMB means
            # paid registration with pending steps (medical cert, etc.)
            status = reg.get("status", "")
            if status and status not in ("COMPLETED", "VALIDATED", "IN PROGRESS"):
                continue

            # Extract club from metaData
            club_name = ""
            meta = reg.get("metaData", [])
            if isinstance(meta, list):
                for item in meta:
                    if isinstance(item, dict):
                        key = item.get("key", item.get("name", ""))
                        if key in ("STRNOM_CLU", "STRNOMABR_CLU", "club", "utmb_information_club"):
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

# Known slugs to always include (manually curated for club-relevant events).
# These are injected into the slug cache on every run so they are always discovered.
_SEED_SLUGS = {
    "saumur-marathon-de-la-loire-2026",
    "asics-saintelyon-2026",
    "schneider-electric-marathon-de-paris-2026",
    "lavenivici-2026",
    "epopee-royale-2026",
}

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

    # Inject known slugs (manually curated)
    if _SEED_SLUGS - slugs:
        slugs.update(_SEED_SLUGS)
        _save_slug_cache(slugs)

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
        country = address.get("country", "")
        if city:
            location = city
        elif country and country != "FR":
            location = country
    if not location:
        event_obj = data.get("event", {})
        if isinstance(event_obj, dict):
            addr2 = event_obj.get("address", {})
            if isinstance(addr2, dict):
                location = addr2.get("city", "")

    return {
        "platform": "njuko",
        "url": f"https://in.njuko.com/{slug}",
        "name": name,
        "date": date_str,
        "location": location,
        "source": "njuko-discovery",
        "_edition_id": edition_id,
    }


# --- UTMB discovery (register-utmb.world uses Njuko API) ---

def discover_utmb_races() -> list[dict]:
    """Discover French UTMB World Series events.

    UTMB registration is powered by Njuko under the hood, so discovered
    events are returned with platform='njuko' and scraped by NjukoScraper.

    Steps:
    1. Fetch the UTMB World Series events page and parse __NEXT_DATA__
    2. Filter for French events (countryCode == "FR")
    3. For each event, fetch its page to find the register-utmb.world slug
    4. Return discovery dicts with the registration URL
    """
    events_url = "https://utmb.world/utmb-world-series-events"
    print(f"  [utmb] Fetching {events_url}")

    try:
        resp = requests.get(events_url, headers={"User-Agent": BROWSER_UA}, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  [utmb] Erreur fetch events page: {e}")
        return []

    # Parse __NEXT_DATA__ JSON from the page
    events = _parse_utmb_events(resp.text)
    if not events:
        print("  [utmb] Aucun evenement trouve dans __NEXT_DATA__")
        return []

    # Filter French events
    fr_events = [e for e in events if e.get("countryCode") == "FR"]
    print(f"  [utmb] {len(fr_events)} evenement(s) FR / {len(events)} total")

    if not fr_events:
        return []

    # Resolve registration URLs concurrently
    from concurrent.futures import ThreadPoolExecutor, as_completed

    races = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(_resolve_utmb_event, e): e for e in fr_events}
        for future in as_completed(futures):
            try:
                result = future.result()
                if result:
                    races.append(result)
            except Exception:
                pass

    print(f"  [utmb] {len(races)} course(s) avec lien d'inscription")
    return races


def _parse_utmb_events(html: str) -> list[dict]:
    """Extract event list from __NEXT_DATA__ JSON in the UTMB events page."""
    match = re.search(
        r'<script\s+id="__NEXT_DATA__"\s+type="application/json">(.*?)</script>',
        html,
        re.DOTALL,
    )
    if not match:
        return []

    try:
        next_data = json.loads(match.group(1))
    except (json.JSONDecodeError, ValueError):
        return []

    # Navigate the Next.js data structure to find events.
    # The structure may vary; try several common paths.
    events = []

    # Try pageProps.events, pageProps.races, pageProps.data.events, etc.
    page_props = next_data.get("props", {}).get("pageProps", {})

    # Walk all values in pageProps looking for lists of dicts with event-like keys
    for key, val in page_props.items():
        if isinstance(val, list) and val:
            # Check if items look like events (have name/title and country info)
            sample = val[0] if isinstance(val[0], dict) else None
            if sample and any(
                k in sample for k in ("countryCode", "country", "countryIso")
            ):
                events = val
                break
        elif isinstance(val, dict):
            # Nested: pageProps.data.events or similar
            for subkey, subval in val.items():
                if isinstance(subval, list) and subval:
                    sample = subval[0] if isinstance(subval[0], dict) else None
                    if sample and any(
                        k in sample
                        for k in ("countryCode", "country", "countryIso")
                    ):
                        events = subval
                        break
            if events:
                break

    # Normalize countryCode if only 'country' or 'countryIso' is present
    for e in events:
        if "countryCode" not in e:
            e["countryCode"] = e.get("countryIso", e.get("country", ""))

    return events


def _resolve_utmb_event(event: dict) -> dict | None:
    """Fetch a UTMB event page to find its register-utmb.world registration URL.

    Returns a discovery dict with platform='njuko' or None.
    """
    # Build the event page URL from available fields
    # Common fields: slug, url, tenant, editionUrl
    event_url = None
    for key in ("url", "editionUrl", "eventUrl"):
        val = event.get(key, "")
        if val:
            if val.startswith("http"):
                event_url = val
            elif val.startswith("/"):
                event_url = f"https://utmb.world{val}"
            break

    # Try building URL from slug/tenant
    if not event_url:
        slug = event.get("slug", event.get("tenant", ""))
        if slug:
            event_url = f"https://{slug}.utmb.world/"

    if not event_url:
        return None

    # Fetch the event page to find registration link
    try:
        resp = requests.get(
            event_url,
            headers={"User-Agent": BROWSER_UA},
            timeout=15,
            allow_redirects=True,
        )
        resp.raise_for_status()
    except requests.RequestException:
        return None

    # Look for register-utmb.world/{slug} in the page
    reg_match = re.search(
        r'https?://(?:in\.)?register-utmb\.world/([a-zA-Z0-9_-]+)',
        resp.text,
    )
    if not reg_match:
        return None

    reg_slug = reg_match.group(1)
    reg_url = f"https://in.register-utmb.world/{reg_slug}"

    # Extract event metadata
    name = event.get("name", event.get("title", event.get("label", "")))
    if isinstance(name, dict):
        name = name.get("en", name.get("fr", str(name)))

    date_str = ""
    for date_key in ("startDate", "date", "eventDate", "editionDate"):
        raw_date = event.get(date_key, "")
        if raw_date:
            dm = re.match(r"(\d{4}-\d{2}-\d{2})", str(raw_date))
            if dm:
                date_str = dm.group(1)
                break

    location = ""
    city = event.get("city", event.get("location", ""))
    if isinstance(city, dict):
        city = city.get("name", city.get("label", ""))
    if city:
        location = city
    elif event.get("region"):
        location = event["region"]

    return {
        "platform": "njuko",
        "url": reg_url,
        "name": name or reg_slug,
        "date": date_str,
        "location": location,
        "source": "utmb-discovery",
    }
