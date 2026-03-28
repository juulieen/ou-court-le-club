"""Scraper for endurancechrono.com.

Registration lists at: /fr/{event-slug}?list=part&order=club
Event discovery: main page lists upcoming events
Table columns: N dossard, Nom, Sexe, Categorie, Club/Team, Paiement, Certificat
"""

import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from .base import BaseScraper, Member, RaceResult, matches_club, matches_known_member

BASE_URL = "https://www.endurancechrono.com"


class EnduranceChronoScraper(BaseScraper):

    def scrape(self, race_config: dict) -> RaceResult | None:
        url = race_config.get("url", "")
        name = race_config.get("name", "Course inconnue")
        date = race_config.get("date", "")
        location = race_config.get("location", "")

        # Ensure we request the participants list sorted by club
        if "list=part" not in url:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}list=part&order=club"

        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
        except requests.RequestException:
            return None

        members = self._parse_table(resp.text)
        if not members:
            return None

        slug = re.search(r"/fr/([^?]+)", url)
        race_id = slug.group(1) if slug else url.split("/")[-1].split("?")[0]

        return RaceResult(
            id=f"endurancechrono-{race_id}",
            name=name, date=date, location=location,
            platform="endurancechrono", url=url,
            members=members, member_count=len(members),
            last_scraped=datetime.now(timezone.utc).isoformat(),
        )

    def _parse_table(self, html: str) -> list[Member]:
        soup = BeautifulSoup(html, "html.parser")
        table = soup.select_one("table")
        if not table:
            return []

        headers = [th.get_text(strip=True).lower() for th in table.select("thead th, tr:first-child th")]
        club_col = next((i for i, h in enumerate(headers) if "club" in h or "team" in h), None)
        nom_col = next((i for i, h in enumerate(headers) if h == "nom" or "participant" in h), None)

        if club_col is None:
            return []

        members = []
        seen = set()
        for row in table.select("tbody tr, tr")[1:]:
            cells = row.find_all("td")
            if len(cells) <= club_col:
                continue
            club = cells[club_col].get_text(strip=True)
            # Name might be in one cell or split
            name = cells[nom_col].get_text(strip=True) if nom_col is not None and nom_col < len(cells) else ""

            is_club = club and matches_club(club, self.patterns)
            is_name = matches_known_member(name, self.known_members)
            if not is_club and not is_name:
                continue

            if not name or name in seen:
                continue
            seen.add(name)
            members.append(Member(name=name, bib=""))
        return members


def discover_races() -> list[dict]:
    races = []
    try:
        resp = requests.get(BASE_URL, timeout=10)
        resp.raise_for_status()
    except requests.RequestException:
        print("  [endurancechrono] Erreur page principale")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    seen = set()

    for link in soup.select("a[href*='/fr/']"):
        href = link.get("href", "")
        # Filter for event pages (not static pages)
        if not re.search(r"/fr/[a-z0-9-]+", href):
            continue
        if any(x in href for x in ("/fr/contact", "/fr/tarifs", "/fr/fonctionnalites", "/fr/a-propos")):
            continue
        if href in seen:
            continue
        seen.add(href)

        full_url = href if href.startswith("http") else f"{BASE_URL}{href}"
        name = link.get_text(strip=True) or "Course"
        if len(name) < 4:
            continue

        races.append({
            "platform": "endurancechrono",
            "url": f"{full_url}?list=part&order=club",
            "name": name,
            "date": "",
            "location": "",
            "source": "endurancechrono-discovery",
        })

    print(f"  [endurancechrono] {len(races)} course(s) decouverte(s)")
    return races
