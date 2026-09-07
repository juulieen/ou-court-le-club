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

from .base import BaseScraper, Member, RaceResult, matches_club, matches_known_member, normalize_text

LIVE_BASE = "https://live.ipitos.com"
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
HEADERS = {"User-Agent": BROWSER_UA}

# Accent-stripped keys — _parse_french_date normalizes before lookup.
_MONTHS = {
    "janvier": "01", "fevrier": "02", "mars": "03", "avril": "04",
    "mai": "05", "juin": "06", "juillet": "07", "aout": "08",
    "septembre": "09", "octobre": "10", "novembre": "11", "decembre": "12",
}


def _parse_french_date(text: str) -> str:
    """Parse "dimanche 29 mars 2026" into "2026-03-29". Empty if unparseable."""
    if not text:
        return ""
    match = re.search(r"(\d{1,2})\s+(\w+)\s+(\d{4})", text)
    if not match:
        return ""
    month = _MONTHS.get(normalize_text(match.group(2)).lower(), "")
    if not month:
        return ""
    return f"{match.group(3)}-{month}-{match.group(1).zfill(2)}"


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

        # Download and parse the XML for club members and event metadata
        members, meta = self._parse_clax(clax_url)

        # The .clax header is authoritative: it carries a clean event name, an
        # ISO date, the host town and the list of courses. Prefer it over the
        # values guessed during discovery.
        return RaceResult(
            id=f"ipitos-{slug}",
            name=meta.get("name") or name,
            date=meta.get("date") or date,
            location=meta.get("location") or location,
            platform="ipitos",
            url=url,
            members=members,
            member_count=len(members),
            last_scraped=datetime.now(timezone.utc).isoformat(),
            race_type=meta.get("race_type", ""),
            distances=meta.get("distances", []),
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
            resp = requests.get(live_url, headers=HEADERS, timeout=15, allow_redirects=True)
            resp.raise_for_status()
        except requests.RequestException:
            return None

        # Strategy 1: Find iframe with g-live viewer, extract .clax path from
        # its query string (e.g., src="G-Live/g-live.html?f=../slug/file.clax")
        soup = BeautifulSoup(resp.text, "html.parser")
        for iframe in soup.select("iframe[src]"):
            src = iframe.get("src", "")
            # Extract .clax path from ?f=... parameter
            f_match = re.search(r"[?&]f=([^&]+\.clax)", src)
            if f_match:
                clax_path = f_match.group(1)
                # Resolve relative path: ../slug/file.clax -> slug/file.clax
                while clax_path.startswith("../"):
                    clax_path = clax_path[3:]
                if clax_path.startswith("http"):
                    return clax_path
                return f"{LIVE_BASE}/{clax_path}"

        # Strategy 2: Look for direct .clax link in page (href or src)
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            if ".clax" in href:
                if href.startswith("http"):
                    return href
                if href.startswith("/"):
                    return f"{LIVE_BASE}{href}"
                return f"{LIVE_BASE}/{slug}/{href}"

        # Strategy 3: Regex fallback — match .clax paths (handle apostrophes)
        match = re.search(r'([\w/._-]+\.clax)', resp.text)
        if match:
            clax_path = match.group(1)
            while clax_path.startswith("../"):
                clax_path = clax_path[3:]
            if clax_path.startswith("http"):
                return clax_path
            return f"{LIVE_BASE}/{clax_path}"

        # Strategy 3: Look for direct <a> links to .clax files
        for a in soup.select("a[href*='.clax']"):
            href = a["href"]
            if href.startswith("http"):
                return href
            return f"{LIVE_BASE}/{slug}/{href}"

        return None

    def _parse_clax(self, clax_url: str) -> tuple[list[Member], dict]:
        """Download and parse a .clax XML file for club members and metadata.

        Uses dual matching: club patterns (c attribute) AND known member
        names (n attribute). Also returns the event metadata carried by the
        root <Epreuve> element (see _parse_meta).
        """
        try:
            resp = requests.get(clax_url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
        except requests.RequestException:
            return [], {}

        try:
            root = ElementTree.fromstring(resp.content)
        except ElementTree.ParseError:
            return [], {}

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

        return members, self._parse_meta(root)

    @staticmethod
    def _parse_meta(root) -> dict:
        """Extract event metadata from the root <Epreuve> element.

        The header carries everything the index page lacks:
          nom="Tout Poitiers Court" organisateur="Poitiers" dt1="2026-04-10"
          ids="CAP_trail"
        plus the list of courses in <PropCourses><C crs="10 Km" />.
        """
        meta = {
            "name": (root.get("nom") or "").strip(),
            # The organiser field holds the host town ("Poitiers", "Varrains").
            "location": (root.get("organisateur") or "").strip(),
            # Older editions carry no dt1, only the French "dates" label.
            "date": (root.get("dt1") or "").strip() or _parse_french_date(root.get("dates") or ""),
        }

        # ids="CAP_route" / "CAP_trail" — absent on some events.
        ids = (root.get("ids") or "").lower()
        if "trail" in ids:
            meta["race_type"] = "trail"
        elif "route" in ids:
            meta["race_type"] = "route"

        # <PropCourses><C crs="Semi-Marathon" /><C crs="10 Km" /> — course names,
        # from which distances in km can be read.
        distances = set()
        for course in root.findall(".//PropCourses/C"):
            label = (course.get("crs") or "").strip()
            if not label:
                continue
            for m in re.finditer(r"(\d+(?:[.,]\d+)?)\s*km\b", label, re.IGNORECASE):
                distances.add(round(float(m.group(1).replace(",", ".")), 1))
            # A course named "Semi-Marathon"/"Marathon" with no explicit
            # mileage: same values as main._extract_distances, so both sources
            # agree on 21.0 / 42.0 rather than each picking its own.
            if re.search(r"\bsemi", label, re.IGNORECASE):
                distances.add(21.0)
            elif re.search(r"\bmarathon\b", label, re.IGNORECASE):
                distances.add(42.0)
        meta["distances"] = sorted(distances)

        return meta


# --- Event discovery ---

def discover_races() -> list[dict]:
    """Discover events from live.ipitos.com index page.

    The index page at live.ipitos.com/ lists all events with links
    to their live pages, along with event names and dates.
    """
    races = []
    seen = set()

    try:
        resp = requests.get(f"{LIVE_BASE}/", headers=HEADERS, timeout=15)
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

        # Extract event name from the title div inside the link
        # (live.ipitos.com uses div.name; div.nom is a legacy fallback)
        nom_div = a.select_one("div.name, div.nom")
        if nom_div:
            name = nom_div.get_text(strip=True)
        else:
            name = a.get_text(strip=True)
        if not name or len(name) < 3:
            name = slug.replace("_", " ").replace("-", " ").title()

        # Extract date from the date div inside the link
        # (live.ipitos.com uses div.date; div.dt is a legacy fallback)
        date_str = ""
        dt_div = a.select_one("div.date, div.dt")
        if dt_div:
            # "lundi 6 avril 2026", "dimanche 29 mars 2026"
            date_str = _parse_french_date(dt_div.get_text(strip=True))

        if not date_str:
            # Fallback: look for date patterns in parent text
            parent = a.parent
            if parent:
                parent_text = parent.get_text(" ", strip=True)
                dm = re.search(r"(\d{2})/(\d{2})/(\d{4})", parent_text)
                if dm:
                    date_str = f"{dm.group(3)}-{dm.group(2)}-{dm.group(1)}"

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
