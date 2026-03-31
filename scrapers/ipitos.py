"""Scraper for IPITOS platform.

IPITOS uses Wiclax .clax XML files for race data.
- Event listing: live.ipitos.com/ (HTML index with ~74 events)
- Live data: live.ipitos.com/{slug}/
- Data format: XML with <E> elements per participant:
    n=name, c=club, p=parcours, d=dossard
"""

import re
from datetime import datetime, timezone
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup

from .base import BaseScraper, Member, RaceResult, matches_club, matches_known_member

LIVE_BASE = "https://live.ipitos.com"


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

        # Find the .clax file URL from the event page iframe
        clax_url = self._find_clax_url(slug)
        if not clax_url:
            return None

        # Download and parse the XML for club members
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
        """Extract the event slug from a live.ipitos.com URL."""
        match = re.search(r"live\.ipitos\.com/([^/]+)", url)
        if match:
            return match.group(1)
        return None

    def _find_clax_url(self, slug: str) -> str | None:
        """Find the .clax file URL from the live event page.

        The event page at live.ipitos.com/{slug}/ contains an iframe
        that references the Wiclax viewer, which loads a .clax XML file.
        """
        live_url = f"{LIVE_BASE}/{slug}/"
        try:
            resp = requests.get(live_url, timeout=15, allow_redirects=True)
            resp.raise_for_status()
        except requests.RequestException:
            return None

        # Strategy 1: Look for .clax reference in HTML/JS (direct reference)
        match = re.search(r'["\']([^"\']*\.clax)["\']', resp.text)
        if match:
            clax_path = match.group(1)
            if clax_path.startswith("http"):
                return clax_path
            if clax_path.startswith("../"):
                clax_path = clax_path.replace("../", "")
                return f"{LIVE_BASE}/{clax_path}"
            if clax_path.startswith("/"):
                return f"{LIVE_BASE}{clax_path}"
            return f"{LIVE_BASE}/{slug}/{clax_path}"

        # Strategy 2: Find iframe src, then look for .clax inside the iframe page
        soup = BeautifulSoup(resp.text, "html.parser")
        for iframe in soup.select("iframe[src]"):
            iframe_src = iframe["src"]
            if not iframe_src.startswith("http"):
                if iframe_src.startswith("/"):
                    iframe_src = f"{LIVE_BASE}{iframe_src}"
                else:
                    iframe_src = f"{LIVE_BASE}/{slug}/{iframe_src}"
            try:
                iframe_resp = requests.get(iframe_src, timeout=15, allow_redirects=True)
                iframe_resp.raise_for_status()
            except requests.RequestException:
                continue
            clax_match = re.search(r'["\']([^"\']*\.clax)["\']', iframe_resp.text)
            if clax_match:
                clax_path = clax_match.group(1)
                if clax_path.startswith("http"):
                    return clax_path
                # Resolve relative to iframe URL
                iframe_base = iframe_src.rsplit("/", 1)[0]
                if clax_path.startswith("../"):
                    clax_path = clax_path.replace("../", "")
                    iframe_base = iframe_base.rsplit("/", 1)[0]
                return f"{iframe_base}/{clax_path}"

        # Strategy 3: Look for direct <a> links to .clax files
        for a in soup.select("a[href*='.clax']"):
            href = a["href"]
            if href.startswith("http"):
                return href
            return f"{LIVE_BASE}/{slug}/{href}"

        return None

    def _parse_clax(self, clax_url: str) -> list[Member]:
        """Download and parse a .clax XML file for club members.

        Uses dual matching: club patterns (c attribute) AND known member
        names (n attribute).
        """
        try:
            resp = requests.get(clax_url, timeout=15)
            resp.raise_for_status()
        except requests.RequestException:
            return []

        try:
            root = ElementTree.fromstring(resp.content)
        except ElementTree.ParseError:
            return []

        members = []
        seen = set()

        # <E> elements are participants
        # Attributes: n=name, c=club, p=parcours, d=dossard
        for elem in root.iter("E"):
            name = elem.get("n", "").strip()
            if not name:
                continue

            # Normalize name for dedup
            name_key = name.lower()
            if name_key in seen:
                continue

            club = elem.get("c", "").strip()
            parcours = elem.get("p", "").strip()

            # Dual matching: club pattern OR known member name
            is_club_match = club and matches_club(club, self.patterns)
            is_name_match = matches_known_member(name, self.known_members)

            if is_club_match or is_name_match:
                seen.add(name_key)
                members.append(Member(name=name, bib=parcours))

        return members


# --- Event discovery ---

def discover_races() -> list[dict]:
    """Discover events from live.ipitos.com index page.

    The index page at live.ipitos.com/ lists all events with links
    to their live pages, along with event names and dates.
    """
    races = []
    seen = set()

    try:
        resp = requests.get(f"{LIVE_BASE}/", timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  [ipitos] Erreur acces {LIVE_BASE}/: {e}")
        return races

    soup = BeautifulSoup(resp.text, "html.parser")

    # Find all links to event pages (live.ipitos.com/{slug}/)
    for a in soup.select("a[href]"):
        href = a.get("href", "").strip()
        if not href:
            continue

        # Extract slug from link (relative or absolute)
        slug = None
        # Absolute URL: https://live.ipitos.com/{slug}/
        slug_match = re.search(r"live\.ipitos\.com/([^/]+)/?", href)
        if slug_match:
            slug = slug_match.group(1)
        else:
            # Relative URL: {slug}/ or ./{slug}/
            rel_match = re.match(r"^\.?/?([A-Za-z0-9_-]+)/?$", href)
            if rel_match:
                candidate = rel_match.group(1)
                # Skip non-event links (index, css, js, images, etc.)
                if candidate.lower() in ("index", "css", "js", "img", "images",
                                          "favicon.ico", "robots.txt"):
                    continue
                slug = candidate

        if not slug or slug in seen:
            continue
        seen.add(slug)

        # Extract event name from link text or parent context
        name = a.get_text(strip=True)
        if not name or len(name) < 3:
            name = slug.replace("_", " ").replace("-", " ").title()

        # Try to extract date from surrounding text (parent row/cell)
        date_str = ""
        parent = a.parent
        if parent:
            parent_text = parent.get_text(" ", strip=True)
            # Look for date patterns: DD/MM/YYYY, DD-MM-YYYY, YYYY-MM-DD
            dm = re.search(r"(\d{2})/(\d{2})/(\d{4})", parent_text)
            if dm:
                date_str = f"{dm.group(3)}-{dm.group(2)}-{dm.group(1)}"
            else:
                dm = re.search(r"(\d{4})-(\d{2})-(\d{2})", parent_text)
                if dm:
                    date_str = dm.group(0)

        # Try to extract location from event name or surrounding text
        location = ""

        races.append({
            "platform": "ipitos",
            "url": f"{LIVE_BASE}/{slug}/",
            "name": name,
            "date": date_str,
            "location": location,
            "source": "ipitos-discovery",
        })

    # Filter: only keep events from current year or future
    current_year = datetime.now().year
    filtered = []
    for race in races:
        date_str = race.get("date", "")
        if date_str:
            try:
                year = int(date_str[:4])
                if year < current_year:
                    continue
            except (ValueError, IndexError):
                pass
        filtered.append(race)

    print(f"  [ipitos] {len(filtered)} course(s) decouverte(s)")
    return filtered
