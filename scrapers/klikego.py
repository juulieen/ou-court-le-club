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

# v8 AJAX API returns all upcoming events in a single HTML response
SEARCH_API = "https://www.klikego.com/v8/evenements/search.jsp"

MONTHS_FR = {
    "janv": "01", "févr": "02", "mars": "03", "avr": "04",
    "mai": "05", "juin": "06", "juil": "07", "août": "08",
    "sept": "09", "oct": "10", "nov": "11", "déc": "12",
}


def discover_races() -> list[dict]:
    """Discover all upcoming running events from Klikego's AJAX search API.

    Single GET to /v8/evenements/search.jsp?sport=0 returns all events as HTML.
    """
    try:
        resp = requests.get(
            SEARCH_API,
            params={"sport": "0"},
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Referer": "https://www.klikego.com/recherche",
            },
            timeout=30,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  [klikego] Erreur API search: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    races = []
    seen_ids = set()

    for link in soup.select("a[href*='/inscription/'][aria-label]"):
        race = _parse_event_card_v8(link)
        if race:
            ref_id = race.get("_ref_id", "")
            if ref_id and ref_id not in seen_ids:
                races.append(race)
                seen_ids.add(ref_id)

    print(f"  [klikego] {len(races)} course(s) decouverte(s)")
    return races


def _parse_event_card_v8(link_el) -> dict | None:
    """Parse a single Klikego v8 event card.

    The card wraps a link element with aria-label (event name) and href
    (/inscription/slug/type/ref-id). The parent container holds metadata.
    """
    href = link_el.get("href", "")
    name = link_el.get("aria-label", "").strip()
    if not href or not name:
        return None

    # Extract slug and ref-id from inscription URL
    match = re.search(r"/inscription/([^/]+)/[^/]+/([^/]+)$", href)
    if not match:
        return None

    slug, ref_id = match.group(1), match.group(2)
    inscrits_url = f"https://www.klikego.com/inscrits/{slug}/{ref_id}"

    # Navigate up to the card container (parent of the link)
    card = link_el.parent
    if not card:
        return None

    # Date: text in span with note styling, e.g. "10–11 avr. 2026" or "11 avr. 2026"
    date_str = ""
    for span in card.select("span"):
        text = span.get_text(strip=True)
        # Match "DD month YYYY" or "DD–DD month YYYY"
        dm = re.search(r"(\d{1,2})\s+(\w+)\.?\s+(\d{4})", text)
        if dm:
            day = dm.group(1).zfill(2)
            month_abbr = dm.group(2).rstrip(".")
            year = dm.group(3)
            month = MONTHS_FR.get(month_abbr, "")
            if month:
                date_str = f"{year}-{month}-{day}"
                break

    # Location: "City, Department (XX)" in a div
    location = ""
    for div in card.select("div"):
        text = div.get_text(strip=True)
        loc_match = re.match(r"^([A-ZÀ-Ü].+?),\s*(.+?)\s*\((\d{2,3})\)$", text)
        if loc_match:
            city = loc_match.group(1).strip()
            dept = loc_match.group(3)
            location = f"{city}, {dept}"
            break

    return {
        "platform": "klikego",
        "url": inscrits_url,
        "name": name,
        "date": date_str,
        "location": location,
        "source": "klikego-discovery",
        "_ref_id": ref_id,
    }
