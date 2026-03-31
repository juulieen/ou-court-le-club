"""Scraper for Klikego platform (AJAX POST endpoint).

Event discovery: GET /recherche?sport=0&page={N} (25 events/page, 0-indexed)
Registration list: POST /types/generic/custo/x.running/findInInscrits.jsp
"""

import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from .base import BaseScraper, Member, RaceResult, normalize_text


def _names_match(name1: str, name2: str) -> bool:
    """Check if two names refer to the same person (order-independent)."""
    parts1 = set(normalize_text(name1).lower().split())
    parts2 = set(normalize_text(name2).lower().split())
    # All parts of the shorter name must be in the longer name
    shorter, longer = (parts1, parts2) if len(parts1) <= len(parts2) else (parts2, parts1)
    return shorter.issubset(longer) and len(shorter) >= 2


class KlikegoScraper(BaseScraper):
    """Scrape registered participants from Klikego race pages.

    Klikego loads registrant data via an AJAX POST to findInInscrits.jsp.
    Two search strategies:
    1. Search by club name via "ville" field (works when members fill in club)
    2. Search by known member names via "search" field (fallback)
    """

    SEARCH_URL = "https://www.klikego.com/types/generic/custo/x.running/findInInscrits.jsp"

    def __init__(self, patterns: list[str], known_members: list[str] | None = None):
        super().__init__(patterns)
        self.known_members = known_members or []

    def scrape(self, race_config: dict) -> RaceResult | None:
        url = race_config.get("url", "")
        name = race_config.get("name", "Course inconnue")
        date = race_config.get("date", "")
        location = race_config.get("location", "")

        reference = self._extract_reference(url)
        if not reference:
            print(f"  [klikego] Impossible d'extraire la reference depuis {url}")
            return None

        session = requests.Session()

        # Get available courses (with labels) to enrich bib info
        courses = self._get_courses(url, session)
        course_labels = {val: label for val, label in courses}

        if courses:
            # Search per course to get proper bib labels
            all_members = []
            seen = set()
            for course_val, course_label in courses:
                found = self._fetch_registrants(reference, course_val, url, session)
                for m in found:
                    if m.name not in seen:
                        all_members.append(Member(name=m.name, bib=course_label))
                        seen.add(m.name)
        else:
            # No course dropdown — search globally
            all_members = self._fetch_registrants(reference, "", url, session)

        return RaceResult(
            id=f"klikego-{reference}",
            name=name,
            date=date,
            location=location,
            platform="klikego",
            url=url,
            members=all_members,
            member_count=len(all_members),
            last_scraped=datetime.now(timezone.utc).isoformat(),
        )

    def _extract_reference(self, url: str) -> str | None:
        """Extract the reference ID from a Klikego /inscrits/ URL.

        URL format: /inscrits/{event-slug}/{reference-id}
        Reference patterns:
            1477100162748-9         (timestamp-sequence)
            jceb_1377094791298-13   (prefixed)
            cd_1420649838925-12     (prefixed)
        """
        # Match the last path segment after /inscrits/slug/
        match = re.search(r"/inscrits/[^/]+/([a-zA-Z_]*\d[\d_-]+\d)/?$", url)
        if match:
            return match.group(1)
        return None

    def _search_by_names(self, reference: str, page_url: str,
                         session: requests.Session) -> list[Member]:
        """Search for known members by their last name."""
        members = []
        seen = set()

        for full_name in self.known_members:
            # Use last name for search (first token if UPPERCASE, else last token)
            parts = full_name.strip().split()
            if not parts:
                continue
            last_name = parts[0] if parts[0].isupper() else parts[-1]

            try:
                resp = session.post(
                    self.SEARCH_URL,
                    data={
                        "search": last_name,
                        "ville": "",
                        "course": "",
                        "sexe": "",
                        "categorie": "",
                        "favoris": "",
                        "reference": reference,
                        "version": "v6",
                        "page": "0",
                    },
                    headers={
                        "Referer": page_url,
                        "X-Requested-With": "XMLHttpRequest",
                    },
                    timeout=8,
                )
                resp.raise_for_status()
            except requests.RequestException:
                continue

            # Parse results and match against full name
            found = self._parse_table(resp.text, "")
            for m in found:
                # Check if the found name matches the known member
                if m.name.lower() == full_name.lower() or \
                   _names_match(m.name, full_name):
                    if m.name not in seen:
                        members.append(m)
                        seen.add(m.name)

        return members

    def _get_courses(self, page_url: str, session: requests.Session) -> list[tuple[str, str]]:
        """Fetch the /inscrits/ page and extract epreuve options.

        Returns list of (value, label) tuples.
        """
        try:
            resp = session.get(page_url, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"  [klikego] Erreur chargement page: {e}")
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        select = soup.select_one("select#course")
        if not select:
            return []

        courses = []
        for opt in select.find_all("option"):
            val = opt.get("value", "").strip()
            label = opt.get_text(strip=True)
            if val:
                courses.append((val, label))
        return courses

    def _fetch_registrants(self, reference: str, course: str, page_url: str,
                           session: requests.Session) -> list[Member]:
        """POST to findInInscrits.jsp and search for club members.

        Uses the session established by _get_courses to maintain JSESSIONID.
        Paginates through results (36 rows/page) until no more data.
        """
        members = []

        # Search using each club pattern as the "ville" (club/city) field
        search_terms = list(dict.fromkeys(
            re.sub(r"[\\^$.*+?()[\]{}|]", "", p.replace("\\s*", " ").replace("\\s+", " ")).strip()
            for p in self.patterns
            if re.sub(r"[\\^$.*+?()[\]{}|]", "", p.replace("\\s*", " ").replace("\\s+", " ")).strip()
        ))

        for search_term in search_terms:
            page = 0
            errored = False
            while True:
                try:
                    resp = session.post(
                        self.SEARCH_URL,
                        data={
                            "search": "",
                            "ville": search_term,
                            "course": course,
                            "sexe": "",
                            "categorie": "",
                            "favoris": "",
                            "reference": reference,
                            "version": "v6",
                            "page": str(page),
                        },
                        headers={
                            "Referer": page_url,
                            "X-Requested-With": "XMLHttpRequest",
                        },
                        timeout=8,
                    )
                    resp.raise_for_status()
                except requests.RequestException:
                    errored = True
                    break

                found = self._parse_table(resp.text, course)
                if not found:
                    break

                existing = {m.name for m in members}
                for m in found:
                    if m.name not in existing:
                        members.append(m)
                        existing.add(m.name)

                # If fewer than 36 rows, we've reached the last page
                if len(found) < 36:
                    break
                page += 1

            # Stop trying other patterns if this event errors out
            if errored:
                break

        return members

    def _parse_table(self, html: str, course_label: str) -> list[Member]:
        """Parse the AJAX response HTML table.

        Table structure: 5 columns per row
        [0] Dossard (bold number)
        [1] Nom & Prenom (with badge "Validee" + country flag img + name)
        [2] Cat. (age category like M2, SE, etc.)
        [3] Ville / Club (format: "CITY (ZIP) / CLUB_NAME" or just "CITY (ZIP)")
        [4] Action button
        """
        members = []
        soup = BeautifulSoup(html, "html.parser")

        for row in soup.select("tr"):
            cells = row.find_all("td")
            if len(cells) < 4:
                continue

            # Skip header rows (contain "Dossard" or "Nom")
            first_text = cells[0].get_text(strip=True).lower()
            if "dossard" in first_text or "nom" in first_text:
                continue

            bib = cells[0].get_text(strip=True)

            # Name cell: remove badge divs and flag images, keep just the text name
            name_cell = cells[1]
            for tag in name_cell.select("div.badge, span.badge, span.label, img"):
                tag.decompose()
            name = name_cell.get_text(strip=True)
            name = re.sub(r"\s+", " ", name).strip()

            if not name:
                continue

            # Use course_label as bib info if available, otherwise use bib number
            bib_info = course_label if course_label else bib
            members.append(Member(name=name, bib=bib_info))

        return members


# --- Event discovery ---

SEARCH_BASE = "https://www.klikego.com/recherche"


def discover_races() -> list[dict]:
    """Discover all upcoming running events from Klikego's search page.

    Paginates through /recherche?sport=0&page={N} (25 events/page, 0-indexed).
    Returns race configs compatible with the main orchestrator.
    """
    races = []
    seen_ids = set()
    page = 0

    while True:
        url = f"{SEARCH_BASE}?sport=0&page={page}"
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"  [klikego] Erreur liste page {page}: {e}")
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select(".card-evenement")
        if not cards:
            break

        for card in cards:
            race = _parse_event_card(card)
            if race:
                eid = race.get("_ref_id", "")
                if eid not in seen_ids:
                    races.append(race)
                    seen_ids.add(eid)

        page += 1

    print(f"  [klikego] {len(races)} course(s) decouverte(s)")
    return races


def _parse_event_card(card) -> dict | None:
    """Parse a single Klikego event card.

    Structure:
      div.card-evenement[id=REFERENCE_ID]
        span.badge-dark          -> DATE "21/03"
        a.texte-vert-fonce       -> NAME (text) + inscription URL (href)
        div                      -> LOCATION "City (dept)"
    """
    ref_id = card.get("id", "")
    if not ref_id:
        return None

    # Event name + URL
    name_link = card.select_one("a.texte-vert-fonce")
    if not name_link:
        return None

    name = name_link.get_text(strip=True)
    href = name_link.get("href", "")

    if not name or not href:
        return None

    # Build inscrits URL: extract slug and ref-id from the inscription href
    # href formats:
    #   /inscription/slug/sport-type/ref-id
    #   https://www.klikego.com/inscription/slug/sport-type/ref-id
    match = re.search(r"/inscription/([^/]+)/[^/]+/([^/]+)$", href)
    if match:
        slug, ref_id_from_url = match.group(1), match.group(2)
        full_url = f"https://www.klikego.com/inscrits/{slug}/{ref_id_from_url}"
    else:
        # Fallback: use ref_id from the card
        full_url = f"https://www.klikego.com/inscrits/{name.lower().replace(' ', '-')}/{ref_id}"

    # Date
    date_str = ""
    date_badge = card.select_one(".badge-dark")
    if date_badge:
        date_text = date_badge.get_text(strip=True)  # "21/03"
        dm = re.match(r"(\d{2})/(\d{2})", date_text)
        if dm:
            # Assume current or next year
            year = datetime.now().year
            date_str = f"{year}-{dm.group(2)}-{dm.group(1)}"

    # Location
    location = ""
    body = card.select_one(".card-body.text-center")
    if body:
        divs = body.select(":scope > div")
        if divs:
            loc_text = divs[0].get_text(strip=True)
            loc_match = re.match(r"(.+?)\s*\((\d{2,3})\)", loc_text)
            if loc_match:
                location = f"{loc_match.group(1).strip()}, {loc_match.group(2)}"
            elif loc_text:
                location = loc_text

    return {
        "platform": "klikego",
        "url": full_url,
        "name": name,
        "date": date_str,
        "location": location,
        "source": "klikego-discovery",
        "_ref_id": ref_id,
    }
