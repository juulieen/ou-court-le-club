"""Scraper for OnSinscrit platform.

Registration lists are at: https://{event-slug}.onsinscrit.com/listeinscrits.php?tous=1&dossards=1
The page uses jQuery DataTables to render an HTML table. Typical columns:
    [0] Nom de famille/Prénom  (single combined column: "LASTNAME Firstname")
    [1] Dossard affecté
    [2] Catégorie
    [3] Nom du groupe/club ou entreprise
    [4] PPS FFA / licence
    [5] Attest. mineur/certif
    [6] Distance

National event discovery via: https://search.onsinscrit.com/evenements.php?p={page}
"""

import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from .base import BaseScraper, Member, RaceResult


class OnSinscritScraper(BaseScraper):
    """Scrape registered participants from OnSinscrit event pages."""

    def scrape(self, race_config: dict) -> RaceResult | None:
        url = race_config.get("url", "")
        name = race_config.get("name", "Course inconnue")
        date = race_config.get("date", "")
        location = race_config.get("location", "")

        list_url = self._build_list_url(url)
        if not list_url:
            return None

        try:
            resp = requests.get(list_url, timeout=10)
            resp.raise_for_status()
        except requests.RequestException:
            return None

        members = self._parse_registrants(resp.text)

        slug = self._extract_slug(url)
        return RaceResult(
            id=f"onsinscrit-{slug}",
            name=name,
            date=date,
            location=location,
            platform="onsinscrit",
            url=url,
            members=members,
            member_count=len(members),
            last_scraped=datetime.now(timezone.utc).isoformat(),
        )

    def _build_list_url(self, url: str) -> str | None:
        """Build the full registration list URL with all entries + bib numbers."""
        url = url.rstrip("/")
        if "listeinscrits.php" in url:
            if "tous=1" not in url:
                url += ("&" if "?" in url else "?") + "tous=1&dossards=1"
            return url

        # Subdomain pattern: https://{slug}.onsinscrit.com
        match = re.match(r"(https?://[^/]+\.onsinscrit\.com)", url)
        if match:
            return f"{match.group(1)}/listeinscrits.php?tous=1&dossards=1"

        # Portal pattern: https://inscriptions.onsinscrit.com/YYYY/slug/
        match = re.match(r"(https?://inscriptions\.onsinscrit\.com/\d{4}/[^/]+)", url)
        if match:
            return f"{match.group(1)}/listeinscrits.php?tous=1&dossards=1"

        return None

    def _extract_slug(self, url: str) -> str:
        """Extract event slug from OnSinscrit URL."""
        match = re.search(r"https?://([^.]+)\.onsinscrit\.com", url)
        if match:
            return match.group(1)
        match = re.search(r"onsinscrit\.com/\d{4}/([^/]+)", url)
        if match:
            return match.group(1)
        return url.rstrip("/").split("/")[-1]

    def _parse_registrants(self, html: str) -> list[Member]:
        """Parse the registration list HTML table to find club members."""
        soup = BeautifulSoup(html, "html.parser")
        members = []
        seen = set()

        table = soup.select_one("table#listeinscrits, table.dataTable, table")
        if not table:
            return []

        # Find column indices from headers
        headers = []
        header_row = table.select_one("thead tr") or table.select_one("tr")
        if header_row:
            headers = [th.get_text(strip=True).lower() for th in header_row.find_all(["th", "td"])]

        col_map = self._map_columns(headers)

        rows = table.select("tbody tr") or table.select("tr")[1:]
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 3:
                continue

            club = self._get_cell(cells, col_map.get("club"))
            name = self._get_cell(cells, col_map.get("nom"))
            if not name or name in seen:
                continue

            # Match by club name OR by known member name
            from .base import matches_club, matches_known_member
            is_club_match = club and matches_club(club, self.patterns)
            is_name_match = matches_known_member(name, self.known_members)

            if not is_club_match and not is_name_match:
                continue
            seen.add(name)

            epreuve = self._get_cell(cells, col_map.get("distance"))
            members.append(Member(name=name, bib=epreuve))

        return members

    def _map_columns(self, headers: list[str]) -> dict[str, int]:
        """Map semantic column names to their indices."""
        col_map = {}

        for i, h in enumerate(headers):
            if ("nom" in h and ("famille" in h or "prénom" in h or "prenom" in h)):
                col_map["nom"] = i
            elif "club" in h or "groupe" in h or "entreprise" in h or "equipe" in h:
                col_map["club"] = i
            elif "distance" in h or "epreuve" in h or "épreuve" in h:
                col_map["distance"] = i
            elif "categ" in h or "catég" in h:
                col_map["cat"] = i
            elif "dossard" in h or "bib" in h:
                col_map["dossard"] = i

        if "nom" not in col_map:
            for i, h in enumerate(headers):
                if "nom" in h and i not in col_map.values():
                    col_map["nom"] = i
                    break

        if "nom" not in col_map:
            col_map = {"nom": 0, "dossard": 1, "cat": 2, "club": 3, "distance": 6}

        return col_map

    def _get_cell(self, cells, col_idx) -> str:
        """Safely get text from a cell by column index."""
        if col_idx is not None and col_idx < len(cells):
            return cells[col_idx].get_text(strip=True)
        return ""


# --- National event discovery ---

EVENTS_URL = "https://search.onsinscrit.com/evenements.php"

MONTHS_FR = {
    "janvier": "01", "février": "02", "mars": "03", "avril": "04",
    "mai": "05", "juin": "06", "juillet": "07", "août": "08",
    "aout": "08", "septembre": "09", "octobre": "10", "novembre": "11",
    "décembre": "12", "decembre": "12",
}


def discover_races() -> list[dict]:
    """Discover upcoming events from OnSinscrit's national directory.

    Paginates through search.onsinscrit.com/evenements.php?p={N}
    starting at p=1 (current/upcoming events).
    """
    races = []
    seen_slugs = set()
    page = 1

    while True:
        try:
            resp = requests.get(f"{EVENTS_URL}?p={page}", timeout=10)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"  [onsinscrit] Erreur liste page {page}: {e}")
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        events = soup.select(".row")
        found = 0

        for row in events:
            race = _parse_event_row(row)
            if race:
                slug = race.get("_slug", "")
                if slug and slug not in seen_slugs:
                    races.append(race)
                    seen_slugs.add(slug)
                    found += 1

        if found == 0:
            break

        page += 1

    print(f"  [onsinscrit] {len(races)} course(s) decouverte(s)")
    return races


def _parse_event_row(row) -> dict | None:
    """Parse a single event row from the OnSinscrit events directory."""
    # Look for event-text content
    text_el = row.select_one(".event-text")
    if not text_el:
        return None

    # Event name: usually in a heading or strong/bold
    name = ""
    for tag in text_el.select("h4, h3, h2, strong, b, a"):
        t = tag.get_text(strip=True)
        if t and len(t) > 3:
            name = t
            break

    if not name:
        return None

    # Get all text content for parsing
    full_text = text_el.get_text(" ", strip=True)

    # Date: look for DD-MM-YYYY or "DD month YYYY"
    date_str = ""
    dm = re.search(r"(\d{2})-(\d{2})-(\d{4})", full_text)
    if dm:
        date_str = f"{dm.group(3)}-{dm.group(2)}-{dm.group(1)}"
    else:
        # Try "DD month YYYY" pattern
        for month_name, month_num in MONTHS_FR.items():
            pattern = rf"(\d{{1,2}})\s+{month_name}\s+(\d{{4}})"
            dm = re.search(pattern, full_text, re.IGNORECASE)
            if dm:
                day = dm.group(1).zfill(2)
                date_str = f"{dm.group(2)}-{month_num}-{day}"
                break

    # Location: look for "CITY (dept)" pattern
    location = ""
    loc_match = re.search(r"([A-ZÀ-Ü][A-ZÀ-Ü\s'-]+)\s*\((\d{2,3})\)", full_text)
    if loc_match:
        city = loc_match.group(1).strip().title()
        dept = loc_match.group(2)
        location = f"{city}, {dept}"

    # Short link: onsinscr.it/slug
    slug = ""
    url = ""
    for link in text_el.select("a[href]"):
        href = link.get("href", "")
        if "onsinscr.it" in href:
            slug_match = re.search(r"onsinscr\.it/(.+?)/?$", href)
            if slug_match:
                slug = slug_match.group(1)
            break
        if "onsinscrit.com" in href:
            url = href
            slug_match = re.search(r"//([^.]+)\.onsinscrit\.com", href)
            if slug_match:
                slug = slug_match.group(1)
            else:
                slug_match = re.search(r"onsinscrit\.com/\d{4}/([^/]+)", href)
                if slug_match:
                    slug = slug_match.group(1)
            break

    if not slug:
        # Derive slug from name
        slug = re.sub(r"[^a-z0-9-]", "-", name.lower())
        slug = re.sub(r"-+", "-", slug).strip("-")

    # Build URL if not found
    if not url:
        # Try subdomain pattern first (most common)
        url = f"https://{slug}.onsinscrit.com/"

    return {
        "platform": "onsinscrit",
        "url": url,
        "name": name,
        "date": date_str,
        "location": location,
        "source": "onsinscrit-directory",
        "_slug": slug,
    }
