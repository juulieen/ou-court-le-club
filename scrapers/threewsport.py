"""Scraper for 3wsport.com.

Registration lists at: /competitor/list/{eventToken}
Event discovery: /courses#allraces (filterable by department)
Table structure: 3rd table on page, columns include Club at index 7.
"""

import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from .base import BaseScraper, Member, RaceResult, matches_club, matches_known_member

BASE_URL = "https://www.3wsport.com"


class ThreeWSportScraper(BaseScraper):
    """Scrape participants from 3wsport.com registration lists."""

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

        token = url.rstrip("/").split("/")[-1]
        return RaceResult(
            id=f"3wsport-{token}",
            name=name,
            date=date,
            location=location,
            platform="3wsport",
            url=url,
            members=members,
            member_count=len(members),
            last_scraped=datetime.now(timezone.utc).isoformat(),
        )

    def _parse_table(self, html: str) -> list[Member]:
        """Parse the 3rd table on the page for club members.

        Columns: Date inscription, Course, Nom, Prenom, Cat., Pays, Dep., Club, ...
        """
        soup = BeautifulSoup(html, "html.parser")
        tables = soup.select("table")
        if len(tables) < 3:
            return []

        table = tables[2]  # 3rd table has the data
        members = []
        seen = set()

        # Find column indices from first row
        headers = []
        first_row = table.select_one("tr")
        if first_row:
            headers = [td.get_text(strip=True).lower() for td in first_row.find_all(["td", "th"])]

        club_col = next((i for i, h in enumerate(headers) if "club" in h), 7)
        nom_col = next((i for i, h in enumerate(headers) if h == "nom"), 2)
        prenom_col = next((i for i, h in enumerate(headers) if "prenom" in h or "prénom" in h), 3)
        course_col = next((i for i, h in enumerate(headers) if "course" in h), 1)

        for row in table.select("tr")[1:]:
            cells = row.find_all("td")
            if len(cells) <= club_col:
                continue

            club = cells[club_col].get_text(strip=True)
            nom = cells[nom_col].get_text(strip=True) if nom_col < len(cells) else ""
            prenom = cells[prenom_col].get_text(strip=True) if prenom_col < len(cells) else ""
            name = f"{prenom} {nom}".strip()

            is_club = club and matches_club(club, self.patterns)
            is_name = matches_known_member(name, self.known_members)
            if not is_club and not is_name:
                continue

            if not name or name in seen:
                continue
            seen.add(name)

            course = cells[course_col].get_text(strip=True) if course_col < len(cells) else ""
            members.append(Member(name=name, bib=course))

        return members


# --- Event discovery ---

def discover_races() -> list[dict]:
    """Discover events from 3wsport.com filtered by departments near Vienne."""
    races = []
    seen = set()

    # Scan relevant departments
    for dept in ["86", "79", "37", "87", "36", "16", "17", "85", "49", "44", "33"]:
        try:
            resp = requests.get(
                f"{BASE_URL}/courses",
                params={"department": dept},
                timeout=10,
            )
            resp.raise_for_status()
        except requests.RequestException:
            continue

        soup = BeautifulSoup(resp.text, "html.parser")

        for link in soup.select("a[href*='/competitor/list/']"):
            href = link.get("href", "")
            if href in seen:
                continue
            seen.add(href)

            full_url = href if href.startswith("http") else f"{BASE_URL}{href}"
            name = link.get_text(strip=True) or "Course 3wsport"

            # Try to find date/location near the link
            parent = link.parent
            date_str = ""
            location = ""
            if parent:
                text = parent.get_text(" ", strip=True)
                dm = re.search(r"(\d{2})/(\d{2})/(\d{4})", text)
                if dm:
                    date_str = f"{dm.group(3)}-{dm.group(2)}-{dm.group(1)}"

            races.append({
                "platform": "3wsport",
                "url": full_url,
                "name": name,
                "date": date_str,
                "location": location,
                "source": "3wsport-discovery",
            })

    print(f"  [3wsport] {len(races)} course(s) decouverte(s)")
    return races
