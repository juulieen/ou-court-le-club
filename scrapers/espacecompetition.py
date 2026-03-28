"""Scraper for espace-competition.com.

Registration lists at: /index.php?module=sportif&action=inscrits&comp={compId}
Event discovery: /index.php?module=accueil&action=agenda
Table: columns Nom, Prenom, Club, Epreuve. Paginated (100/page).
"""

import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from .base import BaseScraper, Member, RaceResult, matches_club, matches_known_member

BASE_URL = "https://www.espace-competition.com"


class EspaceCompetitionScraper(BaseScraper):
    """Scrape participants from espace-competition.com."""

    def scrape(self, race_config: dict) -> RaceResult | None:
        url = race_config.get("url", "")
        name = race_config.get("name", "Course inconnue")
        date = race_config.get("date", "")
        location = race_config.get("location", "")

        comp_id = self._extract_comp_id(url)
        if not comp_id:
            return None

        members = []
        seen = set()
        page = 1

        while True:
            list_url = (
                f"{BASE_URL}/index.php?module=sportif&action=inscrits"
                f"&comp={comp_id}&page={page}"
            )
            try:
                resp = requests.get(list_url, timeout=10)
                resp.raise_for_status()
            except requests.RequestException:
                break

            found = self._parse_table(resp.text)
            if not found:
                break

            for m in found:
                if m.name not in seen:
                    members.append(m)
                    seen.add(m.name)

            # Check if there's a next page
            if not re.search(rf"page={page + 1}", resp.text):
                break
            page += 1

        if not members:
            return None

        return RaceResult(
            id=f"espacecomp-{comp_id}",
            name=name,
            date=date,
            location=location,
            platform="espace-competition",
            url=url,
            members=members,
            member_count=len(members),
            last_scraped=datetime.now(timezone.utc).isoformat(),
        )

    def _extract_comp_id(self, url: str) -> str | None:
        match = re.search(r"comp=(\d+)", url)
        if match:
            return match.group(1)
        # Maybe just a number
        match = re.search(r"/(\d+)$", url)
        if match:
            return match.group(1)
        return None

    def _parse_table(self, html: str) -> list[Member]:
        """Parse registration table. Columns: Nom, Prenom, Club, Epreuve."""
        soup = BeautifulSoup(html, "html.parser")
        table = soup.select_one("table.table-striped, table.table")
        if not table:
            return []

        members = []
        headers = [th.get_text(strip=True).lower() for th in table.select("thead th, tr:first-child th")]
        nom_col = next((i for i, h in enumerate(headers) if h == "nom"), 0)
        prenom_col = next((i for i, h in enumerate(headers) if "prenom" in h), 1)
        club_col = next((i for i, h in enumerate(headers) if "club" in h), 2)
        epreuve_col = next((i for i, h in enumerate(headers) if "epreuve" in h or "épreuve" in h), 3)

        for row in table.select("tbody tr, tr")[1:]:
            cells = row.find_all("td")
            if len(cells) <= club_col:
                continue

            club = cells[club_col].get_text(strip=True)
            nom = cells[nom_col].get_text(strip=True)
            prenom = cells[prenom_col].get_text(strip=True)
            name = f"{prenom} {nom}".strip()

            is_club = club and matches_club(club, self.patterns)
            is_name = matches_known_member(name, self.known_members)
            if not is_club and not is_name:
                continue

            if not name:
                continue

            epreuve = cells[epreuve_col].get_text(strip=True) if epreuve_col < len(cells) else ""
            members.append(Member(name=name, bib=epreuve))

        return members


# --- Event discovery ---

def discover_races() -> list[dict]:
    """Discover events from espace-competition.com agenda."""
    races = []
    seen = set()

    try:
        resp = requests.get(
            f"{BASE_URL}/index.php?module=accueil&action=agenda",
            timeout=10,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  [espace-competition] Erreur: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")

    for link in soup.select("a[href*='comp=']"):
        href = link.get("href", "")
        match = re.search(r"comp=(\d+)", href)
        if not match:
            continue

        comp_id = match.group(1)
        if comp_id in seen:
            continue
        seen.add(comp_id)

        name = link.get_text(strip=True) or f"Course {comp_id}"
        inscrits_url = (
            f"{BASE_URL}/index.php?module=sportif&action=inscrits&comp={comp_id}"
        )

        # Try to find date nearby
        date_str = ""
        parent = link.parent
        if parent:
            text = parent.get_text(" ", strip=True)
            dm = re.search(r"(\d{2})/(\d{2})/(\d{4})", text)
            if dm:
                date_str = f"{dm.group(3)}-{dm.group(2)}-{dm.group(1)}"

        races.append({
            "platform": "espace-competition",
            "url": inscrits_url,
            "name": name,
            "date": date_str,
            "location": "",
            "source": "espacecomp-discovery",
        })

    print(f"  [espace-competition] {len(races)} course(s) decouverte(s)")
    return races
