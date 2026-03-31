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
INSCRIPTIONS_URL = "https://inscription.sportips.fr/api/v2/endpoints/public/inscriptions/get.php"


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
        """Fetch from the new JSON API.

        Two-step process:
        1. Call load.php?base={CODE} to get the id_module
        2. Call get.php?id_module={id}&search=TERM to search for members
        """
        # Step 1: get id_module from load.php
        try:
            resp = requests.get(f"{API_URL}?base={code}", timeout=10)
            if resp.status_code != 200:
                return None
            data = resp.json()
        except Exception:
            return None

        # Extract id_module from response (module.id)
        id_module = None
        if isinstance(data, dict):
            module = data.get("module")
            if isinstance(module, dict):
                id_module = module.get("id")
        if not id_module:
            return None

        members = []
        seen = set()

        # The Sportips "search" API parameter searches across name fields
        # but NOT club. For club matching we'd need to paginate all 7000+
        # participants which is too slow. Instead we search by each known
        # member's last name (fast, targeted) and validate matches.

        # Search by each known member's last name
        for full_name in self.known_members:
            parts = full_name.strip().split()
            if not parts:
                continue
            # Use last name for search (first token if UPPERCASE, else last token)
            last_name = parts[0] if parts[0].isupper() else parts[-1]

            found = self._search_inscriptions(id_module, last_name)
            for name, bib in found:
                if name in seen:
                    continue
                # Validate: must match a known member OR have a matching club
                if matches_known_member(name, self.known_members):
                    members.append(Member(name=name, bib=bib))
                    seen.add(name)

        # Also do a broad club search with a small page size to catch
        # members not in known_members who filled the club field
        search_terms = self._get_club_search_terms()
        for term in search_terms:
            found = self._search_inscriptions(id_module, term)
            for name, bib in found:
                if name not in seen:
                    members.append(Member(name=name, bib=bib))
                    seen.add(name)

        return members

    def _get_club_search_terms(self) -> list[str]:
        """Derive simple search terms from regex club patterns."""
        terms = []
        seen = set()
        for pattern in self.patterns:
            term = pattern.replace("\\s*", " ").replace("\\s+", " ")
            term = re.sub(r"[\\^$.*+?()[\]{}|'?]", "", term).strip()
            if term and term not in seen:
                terms.append(term)
                seen.add(term)
        return terms

    def _search_inscriptions(self, id_module, search_term: str) -> list[tuple[str, str]]:
        """Search inscriptions API for a given term, handling pagination.

        Returns list of (name, bib) tuples for matching participants.
        """
        results = []
        page = 1

        while True:
            try:
                resp = requests.get(
                    INSCRIPTIONS_URL,
                    params={
                        "id_module": id_module,
                        "search": search_term,
                        "perPage": 100,
                        "page": page,
                    },
                    timeout=10,
                )
                if resp.status_code != 200:
                    break
                data = resp.json()
            except Exception:
                break

            # Extract participant list from the response
            participants = []
            if isinstance(data, dict):
                participants = data.get("list", data.get("data", data.get("inscriptions", [])))
                if isinstance(participants, dict):
                    participants = participants.get("data", [])
            elif isinstance(data, list):
                participants = data

            if not participants:
                break

            for p in participants:
                if not isinstance(p, dict):
                    continue

                nom = p.get("nom", p.get("Nom", ""))
                prenom = p.get("prenom", p.get("Prenom", ""))
                club = p.get("club", p.get("Club", ""))
                name = f"{prenom} {nom}".strip()

                if not name:
                    continue

                # Validate: must match club pattern or be a known member
                is_club = club and matches_club(club, self.patterns)
                is_name = matches_known_member(name, self.known_members)
                if not is_club and not is_name:
                    continue

                course = p.get("course", p.get("parcours", p.get("epreuve", "")))
                results.append((name, course))

            # Check if there are more pages
            total = None
            if isinstance(data, dict):
                total = data.get("total", data.get("totalCount"))
                if total is None:
                    meta = data.get("meta", {})
                    if isinstance(meta, dict):
                        total = meta.get("total")

            if total is not None:
                try:
                    if page * 100 >= int(total):
                        break
                except (ValueError, TypeError):
                    pass

            # If we got fewer than perPage results, we're on the last page
            if len(participants) < 100:
                break

            page += 1

        return results

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
