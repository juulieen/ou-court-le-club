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
    """Discover events from Listino's inscription pages.

    New site structure (v2): /evenements/inscriptions lists all events as cards
    with links to /{event-slug}. Paginated via /evenements/inscriptions/OFFSET.
    Also checks /evenements/resultats for past events with registration lists.
    """
    races = []
    seen = set()

    for page_url_tpl in [
        f"{BASE_URL}/evenements/inscriptions",
        f"{BASE_URL}/evenements/resultats",
    ]:
        offset = 0
        while True:
            url = f"{page_url_tpl}/{offset}" if offset else page_url_tpl
            try:
                resp = requests.get(url, timeout=10)
                resp.raise_for_status()
            except requests.RequestException:
                break

            soup = BeautifulSoup(resp.text, "html.parser")
            found = 0

            # Event cards link to /{slug} or /inscrits/{race_id}/0
            for link in soup.select("a[href]"):
                href = link.get("href", "").strip()
                name = link.get_text(strip=True)

                # Skip non-event links
                if not name or len(name) < 4 or name in ("En savoir +", "Créer mon événement"):
                    continue

                # Match event page links: /slug (no sub-path, not a system page)
                full_url = href if href.startswith("http") else f"{BASE_URL}{href}"
                if not full_url.startswith(BASE_URL + "/"):
                    continue

                path = full_url.replace(BASE_URL, "").strip("/")
                # Skip system pages
                if "/" in path or path in (
                    "", "evenements", "recherche", "blog", "panier",
                    "carte", "contact", "quisommesnous", "inscription",
                    "chrono", "graphisme", "chartegraphique", "cgu", "ppd",
                ) or path.startswith("evenements/") or path.startswith("authentication/"):
                    continue

                if full_url in seen:
                    continue
                seen.add(full_url)
                found += 1

                # Extract date from sibling text (e.g. "Paimpol - Samedi 14 février 2026")
                date_str = ""
                location = ""
                parent = link.parent
                if parent:
                    text = parent.get_text(" ", strip=True)
                    dm = re.search(
                        r"(\d{1,2})\s+(janvier|février|mars|avril|mai|juin|"
                        r"juillet|août|septembre|octobre|novembre|décembre)"
                        r"\s+(\d{4})",
                        text, re.IGNORECASE,
                    )
                    if dm:
                        day = dm.group(1).zfill(2)
                        month_map = {
                            "janvier": "01", "février": "02", "mars": "03",
                            "avril": "04", "mai": "05", "juin": "06",
                            "juillet": "07", "août": "08", "septembre": "09",
                            "octobre": "10", "novembre": "11", "décembre": "12",
                        }
                        month = month_map.get(dm.group(2).lower(), "01")
                        date_str = f"{dm.group(3)}-{month}-{day}"

                    # Location: "City - Jour DD month YYYY"
                    loc_match = re.search(r"^([A-ZÀ-Ÿ][a-zà-ÿ'-]+(?:\s+[a-zà-ÿ'-]+)*)\s*-", text)
                    if loc_match:
                        location = loc_match.group(1).strip()

                races.append({
                    "platform": "listino",
                    "url": full_url,
                    "name": name,
                    "date": date_str,
                    "location": location,
                    "source": "listino-discovery",
                })

            if found == 0:
                break
            offset += 11

    print(f"  [listino] {len(races)} course(s) decouverte(s)")
    return races
