"""Scraper for endurancechrono.com.

Registration lists at: /fr/{event-slug}?list=part&order=club
Event discovery: main page lists upcoming events
Table columns: N dossard, Nom, Sexe, Categorie, Club/Team, Paiement, Certificat

Race info on participant page (div.pull-left):
  <em>Type :</em> <strong>Course nature</strong>
  <em>Distance :</em> <strong>10,000</strong> Km
"""

import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from .base import BaseScraper, Member, RaceResult, matches_club, matches_known_member

BASE_URL = "https://www.endurancechrono.com"

# Map French race type labels to canonical types used by _enrich_race
_RACE_TYPE_MAP = {
    "trail": "trail",
    "course nature": "trail",
    "course a obstacle": "autre",
    "course à obstacle": "autre",
    "course à pied": "route",
    "course a pied": "route",
    "marche nordique": "marche",
    "marche": "marche",
}


def _normalize_race_type(raw: str) -> str:
    """Convert a French race type label to a canonical type."""
    return _RACE_TYPE_MAP.get(raw.lower().strip(), "")


def _parse_distance_km(text: str) -> float | None:
    """Parse a distance string like '10,000' or '12.5' to km float."""
    m = re.search(r"(\d+[,.]?\d*)", text)
    if m:
        return round(float(m.group(1).replace(",", ".")), 1)
    return None


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

        soup = BeautifulSoup(resp.text, "html.parser")

        # Extract race name from page info section (e.g. "10 km")
        race_label = self._extract_race_label(soup)

        members = self._parse_table(soup, bib=race_label)
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

    @staticmethod
    def _extract_race_label(soup: BeautifulSoup) -> str:
        """Extract the race/distance label from the page info section.

        The participant page has a div.pull-left with:
          <h2>10 km</h2>
          <em>Type :</em> <strong>Course nature</strong>
          <em>Distance :</em> <strong>10,000</strong> Km
        We use the <h2> text as the race label for the bib field.
        There are multiple div.pull-left on the page; we need the one
        containing the <em>Type</em> tag.
        """
        for div in soup.select("div.pull-left"):
            if div.select_one("em"):
                h2 = div.select_one("h2")
                return h2.get_text(strip=True) if h2 else ""
        return ""

    def _parse_table(self, soup: BeautifulSoup, bib: str = "") -> list[Member]:
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
            members.append(Member(name=name, bib=bib))
        return members


def _parse_results_section(soup: BeautifulSoup) -> list[dict]:
    """Parse the 'Les résultats précédents' section on the homepage.

    Each entry is a div.media inside #blog with structure:
      <h4 class="media-heading"><a href="/fr/...">Name</a></h4>
      Race_type <strong>Distance Km</strong><br/>
      Terminée le <strong>date</strong> ...
    """
    races = []
    for media in soup.select("#blog div.media"):
        link = media.select_one("h4.media-heading a")
        if not link:
            continue
        href = link.get("href", "")
        if not re.search(r"/fr/[a-zA-Z0-9_-]+/", href):
            continue
        name = link.get_text(strip=True)

        body = media.select_one(".media-body")
        if not body:
            continue

        # Extract race_type and distance from text nodes in media-body.
        # Pattern: "Course nature <strong>10,000 Km</strong>"
        # The race type is the text between the h4 and the first <strong>.
        race_type = ""
        distance = None
        strong_tags = body.select("strong")
        if strong_tags:
            # The first strong after h4 contains the distance value
            first_strong = strong_tags[0]
            # Text before the first strong (after the h4) is the race type
            # Walk previous siblings of the first strong
            prev_text = ""
            for sib in first_strong.previous_siblings:
                if hasattr(sib, "name") and sib.name == "h4":
                    break
                if isinstance(sib, str):
                    prev_text = sib.strip() + prev_text
            race_type = _normalize_race_type(prev_text)

            # Distance: first strong text + "Km" suffix
            dist_text = first_strong.get_text(strip=True)
            distance = _parse_distance_km(dist_text)

        # Extract date from "Terminée le <strong>date</strong>"
        date = ""
        for i, st in enumerate(strong_tags):
            if i >= 1:
                txt = st.get_text(strip=True)
                # Look for a date like "22 mars 2026"
                if re.match(r"\d{1,2}\s+\w+\s+\d{4}", txt):
                    date = txt
                    break

        full_url = href if href.startswith("http") else f"{BASE_URL}{href}"
        # Replace list=all with list=part&order=club for scraping
        full_url = re.sub(r"\?list=all$", "", full_url)

        entry = {
            "platform": "endurancechrono",
            "url": f"{full_url}?list=part&order=club",
            "name": name,
            "date": date,
            "location": "",
            "source": "endurancechrono-discovery",
        }
        if race_type:
            entry["race_type"] = race_type
        if distance:
            entry["distances"] = [distance]
        races.append(entry)
    return races


def _parse_inscriptions_section(soup: BeautifulSoup) -> list[dict]:
    """Parse the 'Inscriptions en cours' section on the homepage.

    Each entry is a div.media inside #comments with structure:
      <h4 class="media-heading"><a href="/inscription/fr/...">Name</a></h4>
      <span class="text-muted">jusqu'au date</span>
    These are event-level (no distance/race_type info on homepage).
    """
    races = []
    for media in soup.select("#comments div.media"):
        link = media.select_one("h4.media-heading a")
        if not link:
            continue
        href = link.get("href", "")
        if "/inscription/fr/" not in href:
            continue
        name = link.get_text(strip=True)
        if len(name) < 4:
            continue

        # Convert inscription URL to participant list URL
        # /inscription/fr/Foulees_Perrier_Vergeze_4 -> /fr/Foulees_Perrier_Vergeze_4
        slug = href.replace("/inscription/fr/", "/fr/")
        full_url = f"{BASE_URL}{slug}"

        races.append({
            "platform": "endurancechrono",
            "url": f"{full_url}?list=part&order=club",
            "name": name,
            "date": "",
            "location": "",
            "source": "endurancechrono-discovery",
        })
    return races


def discover_races() -> list[dict]:
    try:
        resp = requests.get(BASE_URL, timeout=10)
        resp.raise_for_status()
    except requests.RequestException:
        print("  [endurancechrono] Erreur page principale")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")

    # Parse structured sections for race_type + distances
    races = _parse_results_section(soup)
    inscriptions = _parse_inscriptions_section(soup)

    # Deduplicate by URL
    seen = {r["url"] for r in races}
    for insc in inscriptions:
        if insc["url"] not in seen:
            races.append(insc)
            seen.add(insc["url"])

    # Also pick up any /fr/ links not already captured (fallback)
    for link in soup.select("a[href*='/fr/']"):
        href = link.get("href", "")
        if not re.search(r"/fr/[a-z0-9-]+", href):
            continue
        if any(x in href for x in ("/fr/contact", "/fr/tarifs", "/fr/fonctionnalites", "/fr/a-propos")):
            continue
        full_url = href if href.startswith("http") else f"{BASE_URL}{href}"
        candidate_url = f"{full_url}?list=part&order=club"
        if candidate_url in seen:
            continue
        name = link.get_text(strip=True) or "Course"
        if len(name) < 4:
            continue
        # Skip navigation / static pages (numbered slugs like 1-Accueil_1)
        if re.search(r"/fr/\d+-\w", href):
            continue

        races.append({
            "platform": "endurancechrono",
            "url": candidate_url,
            "name": name,
            "date": "",
            "location": "",
            "source": "endurancechrono-discovery",
        })
        seen.add(candidate_url)

    print(f"  [endurancechrono] {len(races)} course(s) decouverte(s)")
    return races
