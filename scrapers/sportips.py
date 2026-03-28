"""Scraper for Sportips.fr.

New system (2025+): JSON API at inscription.sportips.fr/api/v2/endpoints/public/module/load.php?base={CODE}
Old system: HTML at sportips.fr/{CODE}/inscrits.php
Event discovery: scrape homepage for event codes.
"""

import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from .base import BaseScraper, Member, RaceResult, matches_club, matches_known_member

BASE_URL = "https://sportips.fr"
API_URL = "https://inscription.sportips.fr/api/v2/endpoints/public/module/load.php"


class SportipsScraper(BaseScraper):

    def scrape(self, race_config: dict) -> RaceResult | None:
        url = race_config.get("url", "")
        name = race_config.get("name", "Course inconnue")
        date = race_config.get("date", "")
        location = race_config.get("location", "")

        code = self._extract_code(url)
        if not code:
            return None

        # Try new API first, fall back to old HTML
        members = self._scrape_api(code)
        if members is None:
            members = self._scrape_html(code)

        if not members:
            return None

        return RaceResult(
            id=f"sportips-{code}",
            name=name, date=date, location=location,
            platform="sportips", url=url,
            members=members, member_count=len(members),
            last_scraped=datetime.now(timezone.utc).isoformat(),
        )

    def _extract_code(self, url: str) -> str | None:
        # /inscription/CODE or /CODE/inscrits.php or just CODE
        match = re.search(r"/inscription/([A-Za-z0-9]+)", url)
        if match:
            return match.group(1)
        match = re.search(r"/([A-Za-z0-9]+)/inscrits", url)
        if match:
            return match.group(1)
        match = re.search(r"base=([A-Za-z0-9]+)", url)
        if match:
            return match.group(1)
        return None

    def _scrape_api(self, code: str) -> list[Member] | None:
        """Fetch from the new JSON API."""
        try:
            resp = requests.get(f"{API_URL}?base={code}", timeout=10)
            if resp.status_code != 200:
                return None
            data = resp.json()
        except Exception:
            return None

        members = []
        seen = set()

        # The API response structure varies; look for participant arrays
        participants = self._find_participants(data)

        for p in participants:
            club = ""
            if isinstance(p, dict):
                club = p.get("club", p.get("Club", ""))
                if not club:
                    # Try nested fields
                    for key in ("societe", "team", "asso"):
                        club = p.get(key, "")
                        if club:
                            break

            nom = p.get("nom", p.get("Nom", ""))
            prenom = p.get("prenom", p.get("Prenom", ""))
            name = f"{prenom} {nom}".strip()

            is_club = club and matches_club(club, self.patterns)
            is_name = matches_known_member(name, self.known_members)
            if not is_club and not is_name:
                continue

            if not name or name in seen:
                continue
            seen.add(name)

            course = p.get("course", p.get("parcours", ""))
            members.append(Member(name=name, bib=course))

        return members

    def _find_participants(self, data) -> list:
        """Recursively find participant arrays in the API response."""
        if isinstance(data, list):
            # Check if it's a list of participant dicts
            if data and isinstance(data[0], dict) and ("nom" in data[0] or "Nom" in data[0]):
                return data
            # Search nested
            for item in data:
                result = self._find_participants(item)
                if result:
                    return result
        elif isinstance(data, dict):
            # Look for common keys
            for key in ("participants", "inscrits", "data", "list", "engages", "coureurs"):
                if key in data:
                    result = self._find_participants(data[key])
                    if result:
                        return result
            # Try all values
            for v in data.values():
                if isinstance(v, (list, dict)):
                    result = self._find_participants(v)
                    if result:
                        return result
        return []

    def _scrape_html(self, code: str) -> list[Member] | None:
        """Fall back to old HTML system."""
        try:
            resp = requests.get(f"{BASE_URL}/{code}/inscrits.php", timeout=10)
            if resp.status_code != 200:
                return None
        except requests.RequestException:
            return None

        soup = BeautifulSoup(resp.text, "html.parser")
        table = soup.select_one("table")
        if not table:
            return None

        headers = [th.get_text(strip=True).lower() for th in table.select("thead th, tr:first-child th")]
        club_col = next((i for i, h in enumerate(headers) if "club" in h), None)
        nom_col = next((i for i, h in enumerate(headers) if h == "nom"), None)
        prenom_col = next((i for i, h in enumerate(headers) if "prenom" in h), None)

        if club_col is None:
            return None

        members = []
        seen = set()
        for row in table.select("tbody tr, tr")[1:]:
            cells = row.find_all("td")
            if len(cells) <= club_col:
                continue
            club = cells[club_col].get_text(strip=True)
            nom = cells[nom_col].get_text(strip=True) if nom_col is not None and nom_col < len(cells) else ""
            prenom = cells[prenom_col].get_text(strip=True) if prenom_col is not None and prenom_col < len(cells) else ""
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


# --- Event discovery ---

def discover_races() -> list[dict]:
    """Discover events from sportips.fr homepage."""
    try:
        resp = requests.get(BASE_URL, timeout=10)
        resp.raise_for_status()
    except requests.RequestException:
        print("  [sportips] Erreur page principale")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    races = []
    seen = set()

    for link in soup.select("a[href*='/inscription/']"):
        href = link.get("href", "")
        match = re.search(r"/inscription/([A-Za-z0-9]+)", href)
        if not match:
            continue

        code = match.group(1)
        if code in seen:
            continue
        seen.add(code)

        name = link.get_text(strip=True) or code
        races.append({
            "platform": "sportips",
            "url": f"{BASE_URL}/inscription/{code}",
            "name": name,
            "date": "",
            "location": "",
            "source": "sportips-discovery",
        })

    print(f"  [sportips] {len(races)} course(s) decouverte(s)")
    return races
