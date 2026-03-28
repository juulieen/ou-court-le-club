"""Scraper for Listino.fr.

Registration lists at: /slug/inscrits/{race_id}/0
Event discovery: /recherche/evenement (paginated, 11 per page)
Table columns: Dossard, Nom, Categorie, Sexe, Club, Statut
Server-rendered HTML (CodeIgniter).
"""

import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from .base import BaseScraper, Member, RaceResult, matches_club, matches_known_member

BASE_URL = "https://listino.fr"


class ListinoScraper(BaseScraper):

    def scrape(self, race_config: dict) -> RaceResult | None:
        url = race_config.get("url", "")
        name = race_config.get("name", "Course inconnue")
        date = race_config.get("date", "")
        location = race_config.get("location", "")

        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
        except requests.RequestException:
            return None

        members = self._parse_table(resp.text)
        if not members:
            return None

        slug = url.rstrip("/").split("/inscrits")[0].split("/")[-1]
        return RaceResult(
            id=f"listino-{slug}",
            name=name, date=date, location=location,
            platform="listino", url=url,
            members=members, member_count=len(members),
            last_scraped=datetime.now(timezone.utc).isoformat(),
        )

    def _parse_table(self, html: str) -> list[Member]:
        soup = BeautifulSoup(html, "html.parser")
        table = soup.select_one("table")
        if not table:
            return []

        headers = [th.get_text(strip=True).lower() for th in table.select("thead th, tr:first-child th")]
        club_col = next((i for i, h in enumerate(headers) if "club" in h), None)
        nom_col = next((i for i, h in enumerate(headers) if h == "nom" or "participant" in h), None)

        if club_col is None or nom_col is None:
            return []

        members = []
        seen = set()
        for row in table.select("tbody tr, tr")[1:]:
            cells = row.find_all("td")
            if len(cells) <= max(club_col, nom_col):
                continue

            club = cells[club_col].get_text(strip=True)
            name = cells[nom_col].get_text(strip=True)
            if not name or name in seen:
                continue

            is_club = club and matches_club(club, self.patterns)
            is_name = matches_known_member(name, self.known_members)
            if not is_club and not is_name:
                continue

            seen.add(name)
            members.append(Member(name=name, bib=""))
        return members


def discover_races() -> list[dict]:
    races = []
    seen = set()
    offset = 0

    while True:
        url = f"{BASE_URL}/recherche/evenement/{offset}" if offset else f"{BASE_URL}/recherche/evenement"
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
        except requests.RequestException:
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        found = 0

        for link in soup.select("a[href*='/inscrits/']"):
            href = link.get("href", "")
            if href in seen:
                continue
            seen.add(href)
            found += 1

            full_url = href if href.startswith("http") else f"{BASE_URL}{href}"
            name = link.get_text(strip=True) or "Course Listino"

            races.append({
                "platform": "listino",
                "url": full_url,
                "name": name,
                "date": "",
                "location": "",
                "source": "listino-discovery",
            })

        # Also check event page links for events without direct inscrits links
        for link in soup.select("a[href^='/']"):
            href = link.get("href", "")
            if re.match(r"^/[a-z0-9-]+-\d{4}$", href) and href not in seen:
                seen.add(href)
                # Try to find inscrits link on the event page
                event_url = f"{BASE_URL}{href}"
                name = link.get_text(strip=True) or href.strip("/")
                races.append({
                    "platform": "listino",
                    "url": event_url,
                    "name": name,
                    "date": "",
                    "location": "",
                    "source": "listino-discovery",
                    "_needs_inscrits_resolution": True,
                })
                found += 1

        if found == 0:
            break
        offset += 11

    print(f"  [listino] {len(races)} course(s) decouverte(s)")
    return races
