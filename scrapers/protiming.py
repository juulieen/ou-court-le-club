"""Scraper for Protiming platform.

Protiming (protiming.fr) is a race timing/registration platform.
- Event list filterable by department: /Runnings/liste?dep=86
- Registration list: /Runnings/registers/{eventId}
- Club filter via URL: /Runnings/registers/{eventId}/searchclub:{name}/distance:0/category:0
- Table #lstParticipants with columns: Distance, Nom, Prenom, Categorie, Club
- Server-side pagination: /Runnings/registers/{eventId}/page:{n}
"""

import re
from datetime import datetime, timezone
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from .base import BaseScraper, Member, RaceResult, normalize_text


def _names_match(name1: str, name2: str) -> bool:
    """Check if two names refer to the same person (order-independent)."""
    parts1 = set(normalize_text(name1).lower().split())
    parts2 = set(normalize_text(name2).lower().split())
    shorter, longer = (parts1, parts2) if len(parts1) <= len(parts2) else (parts2, parts1)
    return shorter.issubset(longer) and len(shorter) >= 2


class ProtimingScraper(BaseScraper):
    """Scrape registered participants from Protiming event pages."""

    BASE_URL = "https://www.protiming.fr"

    def __init__(self, patterns: list[str], known_members: list[str] | None = None):
        super().__init__(patterns)
        self.known_members = known_members or []

    def scrape(self, race_config: dict) -> RaceResult | None:
        url = race_config.get("url", "")
        name = race_config.get("name", "Course inconnue")
        date = race_config.get("date", "")
        location = race_config.get("location", "")

        event_id = self._extract_event_id(url)
        if not event_id:
            return None

        # Strategy 1: search by club name
        members = self._search_members(event_id)

        # Strategy 2: search by known member last names
        if not members and self.known_members:
            members = self._search_by_names(event_id)

        return RaceResult(
            id=f"protiming-{event_id}",
            name=name,
            date=date,
            location=location,
            platform="protiming",
            url=url,
            members=members,
            member_count=len(members),
            last_scraped=datetime.now(timezone.utc).isoformat(),
        )

    def _extract_event_id(self, url: str) -> str | None:
        """Extract numeric event ID from Protiming URL."""
        match = re.search(r"/(?:registers|detail)/(\d+)", url)
        if match:
            return match.group(1)
        # Maybe just a bare ID
        if url.isdigit():
            return url
        return None

    def _search_members(self, event_id: str) -> list[Member]:
        """Search for club members using the club filter."""
        members = []
        seen = set()

        search_terms = self._get_search_terms()

        for term in search_terms:
            encoded = quote(term)
            page = 1
            errored = False
            while True:
                url = (
                    f"{self.BASE_URL}/Runnings/registers/{event_id}"
                    f"/searchclub:{encoded}/distance:0/category:0/page:{page}"
                )
                try:
                    resp = requests.get(url, timeout=8)
                    resp.raise_for_status()
                except requests.RequestException:
                    errored = True
                    break

                found = self._parse_table(resp.text)
                if not found:
                    break

                for m in found:
                    if m.name not in seen:
                        members.append(m)
                        seen.add(m.name)

                # Check for next page
                if not self._has_next_page(resp.text, page):
                    break
                page += 1

            if errored:
                break

        return members

    def _search_by_names(self, event_id: str) -> list[Member]:
        """Search for known members by last name (limit to first 5 for performance)."""
        members = []
        seen_names = set()

        for full_name in self.known_members:
            parts = full_name.strip().split()
            if not parts:
                continue
            last_name = parts[0] if parts[0].isupper() else parts[-1]

            encoded = quote(last_name)
            url = (
                f"{self.BASE_URL}/Runnings/registers/{event_id}"
                f"/search:{encoded}/distance:0/category:0"
            )
            try:
                resp = requests.get(url, timeout=8)
                resp.raise_for_status()
            except requests.RequestException:
                continue

            found = self._parse_table(resp.text)
            for m in found:
                # Verify the name matches a known member
                if _names_match(m.name, full_name) and m.name not in seen_names:
                    members.append(m)
                    seen_names.add(m.name)

        return members

    def _get_search_terms(self) -> list[str]:
        """Derive simple search terms from regex patterns."""
        terms = set()
        for pattern in self.patterns:
            term = pattern.replace("\\s*", " ").replace("\\s+", " ")
            term = re.sub(r"[\\^$.*+?()[\]{}|]", "", term).strip()
            if term:
                terms.add(term)
        return list(terms)

    def _parse_table(self, html: str) -> list[Member]:
        """Parse #lstParticipants table.

        Columns: [0] Distance, [1] Nom, [2] Prenom, [3] Categorie, [4] Club
        """
        soup = BeautifulSoup(html, "html.parser")
        table = soup.select_one("#lstParticipants")
        if not table:
            return []

        members = []
        for row in table.select("tbody tr"):
            cells = row.find_all("td")
            if len(cells) < 5:
                continue

            distance = cells[0].get_text(strip=True)
            nom = cells[1].get_text(strip=True)
            prenom = cells[2].get_text(strip=True)
            # cells[3] = categorie
            # cells[4] = club (already filtered by URL param)

            name = f"{prenom} {nom}".strip()
            if not name:
                continue

            members.append(Member(name=name, bib=distance))

        return members

    def _has_next_page(self, html: str, current_page: int) -> bool:
        """Check if there's a next page in pagination."""
        soup = BeautifulSoup(html, "html.parser")
        next_page = str(current_page + 1)
        for link in soup.select(f"a[href*='page:{next_page}']"):
            return True
        return False


MONTHS_FR = {
    "janv.": "01", "fév.": "02", "mars": "03", "avr.": "04",
    "mai": "05", "juin": "06", "juil.": "07", "août": "08",
    "sept.": "09", "oct.": "10", "nov.": "11", "déc.": "12",
    "janvier": "01", "février": "02", "avril": "04",
    "juillet": "07", "septembre": "09", "octobre": "10",
    "novembre": "11", "décembre": "12",
}


def discover_races(departments: list[str] | None = None) -> list[dict]:
    """Discover ALL races from Protiming's event list.

    Scans all pages of the event listing. No department filter is applied
    since the URL param doesn't work — we keep all events and let the
    scraper check each one for club members.
    """
    races = []
    seen_ids = set()
    page = 1

    while True:
        url = (
            f"https://www.protiming.fr/Runnings/liste/page:{page}"
            f"/sort:Running.date/direction:asc"
        )
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"  [protiming] Erreur liste page {page}: {e}")
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select("div.panel-container")
        if not cards:
            break

        for card in cards:
            race = _parse_event_card(card)
            if not race:
                continue

            event_id = race.get("_event_id", "")
            if event_id not in seen_ids:
                races.append(race)
                seen_ids.add(event_id)

        # Check for next page
        next_page = soup.select_one(f"a[href*='page:{page + 1}']")
        if not next_page:
            break
        page += 1

    print(f"  [protiming] {len(races)} course(s) decouverte(s)")
    return races


def _parse_event_card(card) -> dict | None:
    """Parse a single Protiming event card.

    Structure:
        div.panel-container
          div.row  (visible summary)
            div > div > div.col-md-12.textleft
              span.Cuprum          -> EVENT NAME
              p                    -> LOCATION "City (dept)"
            div > time.icon
              em                   -> YEAR
              strong               -> MONTH
              span                 -> DAY
          div.row.hide (hidden detail)
            a[href*=registers]     -> REGISTRATION LIST LINK
            a[href*=detail]        -> EVENT DETAIL LINK
    """
    # Event name
    name_el = card.select_one("span.Cuprum")
    if not name_el:
        return None
    name = name_el.get_text(strip=True)
    if not name or len(name) < 3:
        return None

    # Location: "City (dept)" in <p> tag next to the name
    location = ""
    dept = ""
    loc_el = card.select_one(".textleft p, .col-md-12.textleft p")
    if loc_el:
        loc_text = loc_el.get_text(strip=True)
        loc_match = re.match(r"(.+?)\s*\((\d{2,3})\)", loc_text)
        if loc_match:
            city = loc_match.group(1).strip()
            dept = loc_match.group(2)
            location = f"{city}, {dept}"

    # Date from time.icon
    date_str = ""
    time_el = card.select_one("time.icon")
    if time_el:
        year_el = time_el.select_one("em")
        month_el = time_el.select_one("strong")
        day_el = time_el.select_one("span")
        if year_el and month_el and day_el:
            year = year_el.get_text(strip=True)
            month_text = month_el.get_text(strip=True).lower()
            day = day_el.get_text(strip=True).zfill(2)
            month = MONTHS_FR.get(month_text, "")
            if year and month and day:
                date_str = f"{year}-{month}-{day}"

    # Registration list link
    event_id = ""
    reg_link = card.select_one("a[href*='/Runnings/registers/']")
    if reg_link:
        match = re.search(r"/Runnings/registers/(\d+)", reg_link["href"])
        if match:
            event_id = match.group(1)

    # Fall back to detail link
    if not event_id:
        detail_link = card.select_one("a[href*='/Runnings/detail/']")
        if detail_link:
            match = re.search(r"/Runnings/detail/(\d+)", detail_link["href"])
            if match:
                event_id = match.group(1)

    if not event_id:
        return None

    url = f"https://www.protiming.fr/Runnings/registers/{event_id}"

    return {
        "platform": "protiming",
        "url": url,
        "name": name,
        "date": date_str,
        "location": location,
        "source": "protiming",
        "_event_id": event_id,
        "_dept": dept,
    }
