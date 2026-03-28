"""Scraper for TimePulse.fr.

Registration lists at: /evenements/liste-epreuve/{id}/{slug}
Event discovery: /calendrier
Table columns: Pays, Nom, Prenom, Club/Asso., Equipe, Etat du dossier
"""

import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from .base import BaseScraper, Member, RaceResult, matches_club, matches_known_member

BASE_URL = "https://www.timepulse.fr"


class TimePulseScraper(BaseScraper):

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

        slug = url.rstrip("/").split("/")[-1]
        return RaceResult(
            id=f"timepulse-{slug}",
            name=name, date=date, location=location,
            platform="timepulse", url=url,
            members=members, member_count=len(members),
            last_scraped=datetime.now(timezone.utc).isoformat(),
        )

    def _parse_table(self, html: str) -> list[Member]:
        soup = BeautifulSoup(html, "html.parser")
        table = soup.select_one("table")
        if not table:
            return []

        headers = [th.get_text(strip=True).lower() for th in table.select("thead th, tr:first-child th")]
        club_col = next((i for i, h in enumerate(headers) if "club" in h or "asso" in h), None)
        nom_col = next((i for i, h in enumerate(headers) if h == "nom"), None)
        prenom_col = next((i for i, h in enumerate(headers) if "prenom" in h or "prénom" in h), None)

        if club_col is None:
            return []

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


def discover_races() -> list[dict]:
    races = []
    try:
        resp = requests.get(f"{BASE_URL}/calendrier", timeout=10)
        resp.raise_for_status()
    except requests.RequestException:
        print("  [timepulse] Erreur calendrier")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    seen = set()

    for link in soup.select("a[href*='/evenements/']"):
        href = link.get("href", "")
        if "/liste-epreuve/" not in href and "/evenements/" in href:
            # Try to find the registration list link
            pass
        if href in seen:
            continue
        seen.add(href)

        full_url = href if href.startswith("http") else f"{BASE_URL}{href}"
        name = link.get_text(strip=True) or "Course TimePulse"

        races.append({
            "platform": "timepulse",
            "url": full_url,
            "name": name,
            "date": "",
            "location": "",
            "source": "timepulse-discovery",
        })

    print(f"  [timepulse] {len(races)} course(s) decouverte(s)")
    return races
