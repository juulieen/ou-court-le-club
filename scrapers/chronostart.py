"""Scraper for Chrono-Start platform.

Event discovery: WP REST API at chrono-start.com/wp-json/wp/v2/mec-events
Registration lists: chrono-start.fr/Inscription/Course/listing/c/{event_id}
  - Protected by Cloudflare: plain `requests` gets a 403 challenge
    ("Just a moment...") from GitHub Actions. A shared session with one fixed,
    recent Chrome User-Agent passes (15/15 on 2026-11-19). Not cloudscraper:
    it draws a random User-Agent per session and 7 in 12 were challenged,
    the same flaw that left Protiming empty on 17-19 Nov 2026.
  - Table #table_listing; the Club column moves (Dossard and Justificatif
    are optional), so it is found by its header.

A page that cannot be read raises ScrapeError rather than returning nothing:
an unreadable list must not look like "no club member" (main.py then keeps
the last good result). The two legitimate empty pages are recognised by their
message: an expired listing ID, and a course nobody has registered for yet.
"""

import html
import json
import re
import time
import zlib
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from .base import (
    BaseScraper,
    Member,
    RaceResult,
    ScrapeError,
    matches_club,
    matches_known_member,
    record_failure,
)

# Legitimately empty listing pages (sampled 2026-11-19: 26 tables, 5 invalid
# links, 1 course without registrations out of 30 listings).
EMPTY_LISTING_MARKERS = (
    "Ce lien n'est pas valide",
    "Aucune Inscription n'a été effectuée",
)


class ChronoStartScraper(BaseScraper):
    """Scrape registered participants from Chrono-Start."""

    BASE_FR = "https://chrono-start.fr"
    BROWSER_UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
    # One shared session with a recent Chrome UA gets past Cloudflare
    _session = None

    @classmethod
    def _get_session(cls) -> requests.Session:
        if cls._session is None:
            cls._session = requests.Session()
            cls._session.headers.update({
                "User-Agent": cls.BROWSER_UA,
                "Accept-Language": "fr-FR,fr;q=0.9",
            })
        return cls._session

    @classmethod
    def _fetch(cls, url: str, timeout: int = 15, attempts: int = 3) -> requests.Response:
        """GET with the shared session, retrying; ScrapeError once all fail."""
        for attempt in range(attempts):
            try:
                resp = cls._get_session().get(url, timeout=timeout)
                resp.raise_for_status()
                return resp
            except requests.RequestException as e:
                if attempt == attempts - 1:
                    raise ScrapeError(f"{url}: {e}") from e
                time.sleep(2 * (attempt + 1))
        raise AssertionError("unreachable")

    def scrape(self, race_config: dict) -> RaceResult | None:
        url = race_config.get("url", "")
        name = race_config.get("name", "Course inconnue")
        date = race_config.get("date", "")
        location = race_config.get("location", "")

        # Resolve listing ID if needed (for events discovered via WP API)
        if race_config.get("_needs_id_resolution"):
            event_id = self._resolve_listing_id(url)
        else:
            event_id = self._extract_event_id(url)

        if not event_id:
            return None

        list_url = f"{self.BASE_FR}/Inscription/Course/listing/c/{event_id}"

        # Cloudflare-protected: the shared browser-UA session, with retries.
        # Raises ScrapeError: an unreadable list is not an empty one.
        resp = self._fetch(list_url)

        # Chrono-Start events can have multiple courses (distances) behind a
        # single listing ID.  The default page ("Tous les Listing") is
        # unreliable and often only shows the first course.  We detect the
        # sub-course dropdown (<select id="idEp">) and scrape each course
        # individually to ensure we don't miss any participants.
        members = self._scrape_all_courses(resp.text, event_id)

        return RaceResult(
            id=f"chronostart-{event_id}",
            name=name,
            date=date,
            location=location,
            platform="chronostart",
            url=url,
            members=members,
            member_count=len(members),
            last_scraped=datetime.now(timezone.utc).isoformat(),
        )

    def _scrape_all_courses(self, html: str, event_id: str) -> list[Member]:
        """Scrape all sub-courses for a multi-distance event.

        Chrono-Start events can have multiple courses (e.g. 6km, 14km, 33km)
        under one listing ID.  The default "Tous les Listing" page is buggy
        and often only returns participants from the first course.

        This method detects the <select id="idEp"> dropdown, and if multiple
        courses exist, fetches each one individually via ?c={id}&idEp={ep}.
        """
        soup = BeautifulSoup(html, "html.parser")
        select = soup.select_one("select#idEp")

        if not select:
            # No sub-course dropdown: single course, parse directly
            return self._parse_table(html)

        # Collect sub-course IDs and names (skip idEp=0 which is "Tous les Listing")
        courses = []
        for option in select.find_all("option"):
            val = option.get("value", "")
            if val and val != "0":
                course_name = option.get_text(strip=True)
                courses.append((val, course_name))

        if not courses:
            return self._parse_table(html)

        # If only one real course, parse the current page with its name
        if len(courses) == 1:
            return self._parse_table(html, course_name=courses[0][1])

        # Multiple courses: fetch each individually
        all_members = []
        seen = set()

        for ep_id, course_name in courses:
            ep_url = f"{self.BASE_FR}/Inscription/course/listing?c={event_id}&idEp={ep_id}"
            # No skipping a distance that fails: its members would vanish
            # while the race stays on the map with the others.
            resp = self._fetch(ep_url)

            for member in self._parse_table(resp.text, course_name=course_name):
                if member.name not in seen:
                    seen.add(member.name)
                    all_members.append(member)

        return all_members

    def _resolve_listing_id(self, event_page_url: str) -> str | None:
        """Fetch a chrono-start.com event page and extract the listing ID.

        None when the page has no listing; ScrapeError when it is unreadable."""
        resp = self._fetch(event_page_url)
        match = re.search(r"/listing/c/(\d+)", resp.text)
        return match.group(1) if match else None

    def _extract_event_id(self, url: str) -> str | None:
        match = re.search(r"/listing/c/(\d+)", url)
        if match:
            return match.group(1)
        match = re.search(r"[?&]c=(\d+)", url)
        if match:
            return match.group(1)
        if url.isdigit():
            return url
        return None

    def _parse_table(self, html: str, course_name: str = "") -> list[Member]:
        """Parse #table_listing. Columns: idx|Nom|Prenom|Nat|Sexe|Cat|Dossard|Club|..."""
        soup = BeautifulSoup(html, "html.parser")
        members = []
        seen = set()

        table = soup.select_one("#table_listing")
        if not table:
            text = " ".join(soup.get_text(" ").split())
            if any(marker in text for marker in EMPTY_LISTING_MARKERS):
                return []
            raise ScrapeError("page de liste sans tableau ni message connu")

        # Find club column index from headers
        headers = []
        header_row = table.select_one("thead tr")
        if header_row:
            headers = [th.get_text(strip=True).lower() for th in header_row.find_all(["th", "td"])]

        club_col = None
        nom_col = None
        prenom_col = None
        for i, h in enumerate(headers):
            if "club" in h:
                club_col = i
            elif h == "nom":
                nom_col = i
            elif "prenom" in h or "prénom" in h:
                prenom_col = i
        if club_col is None:
            # Relay courses list an "Equipe" instead of a club (event 5515):
            # a team named after the club is still the club.
            club_col = next(
                (i for i, h in enumerate(headers) if h in ("equipe", "équipe")), None
            )

        if club_col is None or nom_col is None or prenom_col is None:
            # The Club column moves between events: guessing a position
            # would match the wrong column without a word.
            raise ScrapeError(f"colonnes inattendues dans la liste: {headers}")

        for row in table.select("tbody tr"):
            cells = row.find_all("td")
            if len(cells) <= max(club_col, nom_col, prenom_col):
                continue

            club = cells[club_col].get_text(strip=True)
            nom = cells[nom_col].get_text(strip=True)
            prenom = cells[prenom_col].get_text(strip=True)
            name = f"{prenom} {nom}".strip()

            is_club = club and matches_club(club, self.patterns)
            is_name = matches_known_member(name, self.known_members)
            if not is_club and not is_name:
                continue

            if not name or name in seen:
                continue
            seen.add(name)

            members.append(Member(name=name, bib=course_name, by_name=not is_club))

        return members


# --- Event discovery via WP REST API ---

WP_API = "https://chrono-start.com/wp-json/wp/v2/mec-events"


def _fetch_wp_page(page: int) -> requests.Response | None:
    """Fetch one page of the WP API, retrying transient failures.

    Returns None when the API says "no such page" (HTTP 400). Raises the
    last RequestException after 3 failed attempts — a single network blip
    must not mute the whole platform (incident du 2026-10-21 : page 1 en
    échec sur le runner CI -> 0 événement, découverte muette sans erreur).
    """
    for attempt in range(3):
        try:
            resp = requests.get(
                WP_API,
                params={"per_page": 100, "page": page},
                timeout=15,
            )
            if resp.status_code == 400:
                return None
            resp.raise_for_status()
            return resp
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))


def discover_races() -> list[dict]:
    """Discover events from Chrono-Start's WordPress REST API.

    Fetches all events, then resolves listing IDs concurrently.
    Only returns events that have a registration listing page.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # Step 1: Fetch all events from WP API
    event_links = []
    wp_complete = False
    page = 1
    while True:
        try:
            resp = _fetch_wp_page(page)
        except requests.RequestException as e:
            print(f"  [chronostart] ERREUR WP API page {page}: {e}")
            record_failure("Chrono-Start", f"API WordPress page {page}")
            break
        if resp is None:
            wp_complete = True
            break

        events = resp.json()
        if not events:
            wp_complete = True
            break

        for event in events:
            title = event.get("title", {}).get("rendered", "")
            link = event.get("link", "")
            if title and link:
                # WP API returns HTML entities in rendered titles
                title = html.unescape(title)
                event_links.append((title, link))

        total_pages = int(resp.headers.get("X-WP-TotalPages", 1))
        if page >= total_pages:
            wp_complete = True
            break
        page += 1

    # Step 2: Resolve listing IDs concurrently, skipping pages seen without a
    # listing recently (see NO_LISTING_DAYS)
    seen = _load_seen()
    today = date.today().isoformat()
    to_check = [(t, link) for t, link in event_links if seen.get(link, "") <= today]
    print(f"  [chronostart] {len(event_links)} evenements WP, "
          f"{len(event_links) - len(to_check)} sans listing ignorés, resolution IDs de {len(to_check)}...")

    races = []
    unreadable = 0
    with ThreadPoolExecutor(max_workers=DISCOVERY_WORKERS) as executor:
        futures = {
            executor.submit(_resolve_event, title, link): link
            for title, link in to_check
        }
        for future in as_completed(futures):
            link = futures[future]
            race = future.result()
            if race == NO_LISTING:
                seen[link] = _recheck_date(link)
            elif race:
                seen.pop(link, None)
                races.append(race)
            else:
                unreadable += 1

    if wp_complete:
        # Forget pages that left the agenda. Not after a partial WP list:
        # the events of the missing pages would lose their memory too.
        live = {link for _, link in event_links}
        _save_seen({k: v for k, v in seen.items() if k in live})
    elif event_links:
        _save_seen(seen)
    print(f"  [chronostart] {len(races)} course(s) avec listing"
          + (f", {unreadable} page(s) d'événement illisible(s)" if unreadable else ""))
    return races


# Of ~630 event pages, only ~30 have a registration list. Opening all of them
# every day (Cloudflare site) looked like a bot: a page without a list is
# opened again after NO_LISTING_DAYS (minus up to 2 days, fixed per page so
# they do not all come back the same day). Kept in its own CI cache.
SEEN_PATH = Path(__file__).resolve().parent.parent / "data" / "chronostart_seen.json"
NO_LISTING = "no-listing"
NO_LISTING_DAYS = 7
# chrono-start.com (Apache) answers 503 to almost everything past ~3
# concurrent requests: with 5, 501 of 637 event pages failed on 2026-11-19,
# silently until then. 1, 2 or 3 at a time: 60/60. Two keeps a margin.
DISCOVERY_WORKERS = 2


def _load_seen() -> dict[str, str]:
    try:
        seen = json.loads(SEEN_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return seen if isinstance(seen, dict) else {}


def _save_seen(seen: dict[str, str]) -> None:
    SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEEN_PATH.write_text(json.dumps(seen, indent=0, sort_keys=True), encoding="utf-8")


def _recheck_date(link: str) -> str:
    spread = zlib.crc32(link.encode()) % 3
    return (date.today() + timedelta(days=NO_LISTING_DAYS - spread)).isoformat()


def _resolve_event(title: str, event_page_url: str) -> dict | str | None:
    """Fetch an event page and extract the listing ID, date, and location.

    NO_LISTING when the page has no registration list; None when it could
    not be read after retries (asked again next run, and reported)."""
    try:
        resp = ChronoStartScraper._fetch(event_page_url, timeout=10)
    except ScrapeError as e:
        record_failure("Chrono-Start", f"page d'événement {e}")
        return None

    match = re.search(r"/listing/c/(\d+)", resp.text)
    if not match:
        return NO_LISTING

    event_id = match.group(1)
    listing_url = f"https://chrono-start.fr/Inscription/Course/listing/c/{event_id}"

    # Decode HTML entities in title (e.g. &rsquo; -> ')
    clean_title = html.unescape(title)

    date_str = ""
    location = ""
    soup = BeautifulSoup(resp.text, "html.parser")

    # Strategy 1: Extract from Event JSON-LD (most reliable)
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string)
            if isinstance(data, dict) and data.get("@type") == "Event":
                if not date_str and data.get("startDate"):
                    # startDate is ISO format like "2026-06-14"
                    date_str = data["startDate"][:10]
                loc_data = data.get("location", {})
                if not location and isinstance(loc_data, dict):
                    location = loc_data.get("name") or loc_data.get("address") or ""
        except (json.JSONDecodeError, TypeError):
            continue

    # Strategy 2: Extract date from MEC HTML (.mec-start-date-label)
    if not date_str:
        date_label = soup.select_one(".mec-start-date-label")
        if date_label:
            date_str = _parse_french_date(date_label.get_text(strip=True))

    # Strategy 3: Extract date from dd/mm/yyyy pattern in page
    if not date_str:
        dm = re.search(r"(\d{2})/(\d{2})/(\d{4})", resp.text[:5000])
        if dm:
            date_str = f"{dm.group(3)}-{dm.group(2)}-{dm.group(1)}"

    # Strategy 2b: Extract location from map markers JSON
    if not location:
        marker_match = re.search(
            r'"name"\s*:\s*"([^"]+)".*?"address"\s*:\s*"([^"]*)"',
            resp.text,
        )
        if marker_match:
            location = marker_match.group(1)

    return {
        "platform": "chronostart",
        "url": listing_url,
        "name": clean_title,
        "date": date_str,
        "location": location,
        "source": "chronostart-discovery",
    }


# French month names -> month numbers for parsing "14 Juin 2026" style dates
_FRENCH_MONTHS = {
    "janvier": "01", "février": "02", "fevrier": "02", "mars": "03",
    "avril": "04", "mai": "05", "juin": "06", "juillet": "07",
    "août": "08", "aout": "08", "septembre": "09", "octobre": "10",
    "novembre": "11", "décembre": "12", "decembre": "12",
}


def _parse_french_date(text: str) -> str:
    """Parse a French date like '14 Juin 2026' into 'YYYY-MM-DD'."""
    text = text.strip().lower()
    match = re.match(r"(\d{1,2})\s+(\w+)\s+(\d{4})", text)
    if not match:
        return ""
    day, month_name, year = match.groups()
    month = _FRENCH_MONTHS.get(month_name, "")
    if not month:
        return ""
    return f"{year}-{month}-{day.zfill(2)}"
