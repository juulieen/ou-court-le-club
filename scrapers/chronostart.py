"""Scraper for Chrono-Start platform.

Event discovery: WP REST API at chrono-start.com/wp-json/wp/v2/mec-events
Registration lists: chrono-start.fr/Inscription/Course/listing/c/{event_id}
  - Protected by Cloudflare (needs cloudscraper)
  - Table #table_listing with Club column (index 7)
"""

import re
from datetime import datetime, timezone

try:
    import cloudscraper
except ImportError:
    cloudscraper = None

import requests
from bs4 import BeautifulSoup

from .base import BaseScraper, Member, RaceResult, matches_club, matches_known_member


class ChronoStartScraper(BaseScraper):
    """Scrape registered participants from Chrono-Start."""

    BASE_FR = "https://chrono-start.fr"
    # Shared cloudscraper session to avoid Cloudflare blocks in concurrent use
    _session = None

    @classmethod
    def _get_session(cls):
        if cls._session is None and cloudscraper:
            cls._session = cloudscraper.create_scraper()
        return cls._session

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

        # Cloudflare-protected: use shared cloudscraper session
        try:
            session = self._get_session()
            if session:
                resp = session.get(list_url, timeout=15)
            else:
                resp = requests.get(list_url, timeout=15)
            resp.raise_for_status()
        except Exception:
            return None

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

        # Collect sub-course IDs (skip idEp=0 which is "Tous les Listing")
        course_ids = []
        for option in select.find_all("option"):
            val = option.get("value", "")
            if val and val != "0":
                course_ids.append(val)

        if not course_ids:
            return self._parse_table(html)

        # If only one real course, parse the current page
        if len(course_ids) == 1:
            return self._parse_table(html)

        # Multiple courses: fetch each individually
        all_members = []
        seen = set()
        session = self._get_session()

        for ep_id in course_ids:
            ep_url = f"{self.BASE_FR}/Inscription/course/listing?c={event_id}&idEp={ep_id}"
            try:
                if session:
                    resp = session.get(ep_url, timeout=15)
                else:
                    resp = requests.get(ep_url, timeout=15)
                resp.raise_for_status()
            except Exception:
                continue

            for member in self._parse_table(resp.text):
                if member.name not in seen:
                    seen.add(member.name)
                    all_members.append(member)

        return all_members

    def _resolve_listing_id(self, event_page_url: str) -> str | None:
        """Fetch a chrono-start.com event page and extract the listing ID."""
        try:
            resp = requests.get(event_page_url, timeout=8)
            resp.raise_for_status()
        except requests.RequestException:
            return None
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

    def _parse_table(self, html: str) -> list[Member]:
        """Parse #table_listing. Columns: idx|Nom|Prenom|Nat|Sexe|Cat|Dossard|Club|..."""
        soup = BeautifulSoup(html, "html.parser")
        members = []
        seen = set()

        table = soup.select_one("#table_listing")
        if not table:
            return []

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
            # Default positions
            nom_col, prenom_col, club_col = 1, 2, 7

        if nom_col is None:
            nom_col = 1
        if prenom_col is None:
            prenom_col = 2

        for row in table.select("tbody tr"):
            cells = row.find_all("td")
            if len(cells) <= max(club_col or 0, nom_col, prenom_col):
                continue

            club = cells[club_col].get_text(strip=True) if club_col is not None else ""
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

            members.append(Member(name=name, bib=""))

        return members


# --- Event discovery via WP REST API ---

WP_API = "https://chrono-start.com/wp-json/wp/v2/mec-events"


def discover_races() -> list[dict]:
    """Discover events from Chrono-Start's WordPress REST API.

    Fetches all events, then resolves listing IDs concurrently.
    Only returns events that have a registration listing page.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # Step 1: Fetch all events from WP API
    event_links = []
    page = 1
    while True:
        try:
            resp = requests.get(
                WP_API,
                params={"per_page": 100, "page": page},
                timeout=15,
            )
            if resp.status_code == 400:
                break
            resp.raise_for_status()
        except requests.RequestException:
            break

        events = resp.json()
        if not events:
            break

        for event in events:
            title = event.get("title", {}).get("rendered", "")
            link = event.get("link", "")
            if title and link:
                event_links.append((title, link))

        total_pages = int(resp.headers.get("X-WP-TotalPages", 1))
        if page >= total_pages:
            break
        page += 1

    print(f"  [chronostart] {len(event_links)} evenements WP, resolution IDs...")

    # Step 2: Resolve listing IDs concurrently
    races = []
    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = {
            executor.submit(_resolve_event, title, link): (title, link)
            for title, link in event_links
        }
        for future in as_completed(futures):
            race = future.result()
            if race:
                races.append(race)

    print(f"  [chronostart] {len(races)} course(s) avec listing")
    return races


def _resolve_event(title: str, event_page_url: str) -> dict | None:
    """Fetch an event page and extract the listing ID."""
    try:
        resp = requests.get(event_page_url, timeout=8)
        resp.raise_for_status()
    except requests.RequestException:
        return None

    match = re.search(r"/listing/c/(\d+)", resp.text)
    if not match:
        return None

    event_id = match.group(1)
    listing_url = f"https://chrono-start.fr/Inscription/Course/listing/c/{event_id}"

    # Try to extract date and location from the page
    date_str = ""
    location = ""
    soup = BeautifulSoup(resp.text, "html.parser")

    # Date often in meta or structured data
    dm = re.search(r"(\d{2})/(\d{2})/(\d{4})", resp.text[:5000])
    if dm:
        date_str = f"{dm.group(3)}-{dm.group(2)}-{dm.group(1)}"

    return {
        "platform": "chronostart",
        "url": listing_url,
        "name": title,
        "date": date_str,
        "location": location,
        "source": "chronostart-discovery",
    }
