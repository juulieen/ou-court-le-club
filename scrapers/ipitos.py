"""Scraper for IPITOS platform.

IPITOS uses Wiclax .clax XML files for race data.
- Event listing: www.ipitos.com/index.php/resultats/competitions.html
- Live data: live.ipitos.com/{slug}/
- Data format: XML with <E> elements per participant, club in 'c' attribute
"""

import re
from datetime import datetime, timezone
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup

from .base import BaseScraper, Member, RaceResult, matches_club


class IpitosScraper(BaseScraper):
    """Scrape participants from IPITOS .clax XML files."""

    def scrape(self, race_config: dict) -> RaceResult | None:
        url = race_config.get("url", "")
        name = race_config.get("name", "Course inconnue")
        date = race_config.get("date", "")
        location = race_config.get("location", "")

        slug = self._extract_slug(url)
        if not slug:
            return None

        # Find the .clax file URL
        clax_url = self._find_clax_url(slug)
        if not clax_url:
            return None

        # Download and parse the XML
        members = self._parse_clax(clax_url)

        return RaceResult(
            id=f"ipitos-{slug}",
            name=name,
            date=date,
            location=location,
            platform="ipitos",
            url=url,
            members=members,
            member_count=len(members),
            last_scraped=datetime.now(timezone.utc).isoformat(),
        )

    def _extract_slug(self, url: str) -> str | None:
        match = re.search(r"live\.ipitos\.com/([^/]+)", url)
        if match:
            return match.group(1)
        # Might be a www.ipitos.com event page
        match = re.search(r"/(\d+)-([^/.]+)\.html", url)
        if match:
            return match.group(2)
        return None

    def _find_clax_url(self, slug: str) -> str | None:
        """Find the .clax file URL from the live page."""
        live_url = f"https://live.ipitos.com/{slug}/"
        try:
            resp = requests.get(live_url, timeout=10, allow_redirects=True)
            resp.raise_for_status()
        except requests.RequestException:
            return None

        # Look for .clax reference in the HTML/JS
        match = re.search(r'["\']([^"\']*\.clax)["\']', resp.text)
        if match:
            clax_path = match.group(1)
            if clax_path.startswith("http"):
                return clax_path
            if clax_path.startswith("../"):
                clax_path = clax_path.replace("../", "")
            return f"https://live.ipitos.com/{clax_path}"

        # Try common pattern: slug/date name.clax
        # Look for any .clax link in the page
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.select("a[href*='.clax']"):
            href = a["href"]
            if href.startswith("http"):
                return href
            return f"https://live.ipitos.com/{slug}/{href}"

        return None

    def _parse_clax(self, clax_url: str) -> list[Member]:
        """Download and parse a .clax XML file for club members."""
        try:
            resp = requests.get(clax_url, timeout=15)
            resp.raise_for_status()
        except requests.RequestException:
            return []

        members = []
        seen = set()

        try:
            root = ElementTree.fromstring(resp.content)
        except ElementTree.ParseError:
            return []

        # <E> elements are participants, club is in 'c' attribute
        for elem in root.iter("E"):
            club = elem.get("c", "")
            if not club or not matches_club(club, self.patterns):
                continue

            name = elem.get("n", "")
            if not name or name in seen:
                continue
            seen.add(name)

            bib = elem.get("p", "")  # parcours/race
            members.append(Member(name=name, bib=bib))

        return members


# --- Event discovery ---

EVENTS_URL = "https://www.ipitos.com/index.php/evenements-a-venir.html"
COMPETITIONS_URL = "https://www.ipitos.com/index.php/resultats/competitions.html"


def discover_races() -> list[dict]:
    """Discover events from IPITOS event listing."""
    races = []
    seen = set()

    # Upcoming events
    _scrape_event_list(EVENTS_URL, races, seen)

    # Recent competitions (first few pages)
    for start in range(0, 100, 20):
        url = f"{COMPETITIONS_URL}?start={start}"
        count = _scrape_event_list(url, races, seen)
        if count == 0:
            break

    print(f"  [ipitos] {len(races)} course(s) decouverte(s)")
    return races


def _scrape_event_list(url: str, races: list, seen: set) -> int:
    """Scrape an IPITOS event list page."""
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
    except requests.RequestException:
        return 0

    soup = BeautifulSoup(resp.text, "html.parser")
    count = 0

    for row in soup.select("table tr"):
        cells = row.find_all("td")
        if len(cells) < 3:
            continue

        # Find link to live results
        link = row.select_one("a[href*='live.ipitos.com']")
        if not link:
            continue

        href = link.get("href", "")
        slug_match = re.search(r"live\.ipitos\.com/([^/]+)", href)
        if not slug_match:
            continue

        slug = slug_match.group(1)
        if slug in seen:
            continue
        seen.add(slug)

        name = link.get_text(strip=True)
        if not name:
            name = slug.replace("_", " ").replace("-", " ").title()

        # Date from first cell
        date_str = ""
        date_text = cells[0].get_text(strip=True)
        dm = re.search(r"(\d{2})/(\d{2})/(\d{4})", date_text)
        if dm:
            date_str = f"{dm.group(3)}-{dm.group(2)}-{dm.group(1)}"

        # Location
        location = ""
        if len(cells) >= 3:
            loc_text = cells[2].get_text(strip=True)
            if loc_text:
                location = loc_text

        races.append({
            "platform": "ipitos",
            "url": f"https://live.ipitos.com/{slug}/",
            "name": name,
            "date": date_str,
            "location": location,
            "source": "ipitos-discovery",
        })
        count += 1

    return count
