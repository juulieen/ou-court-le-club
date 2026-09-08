"""Scraper for Protiming platform.

Protiming (protiming.fr) was rebuilt as a Tailwind SPA (Sept 2026). The old
`/Runnings/*` routes now redirect to `/events`, and `#lstParticipants` /
`searchclub:` are gone.

- Event list: `/events?page={n}` — 14 cards per page, each linking to
  `/events/{id}-{slug}`. Pagination ends when a page repeats known events.
- Registration list: `/events/{id}-{slug}/runners` — server-rendered table,
  50 rows per page, columns: Dossard, Épreuve, Nom/Prénom, Catégorie, Club.
- Server-side filters: `?club={text}` (case-insensitive substring, so "rtv"
  matches "RTV28" and "bonneval judo RTV28") and `?q={text}` (name/bib).

The club filter is what makes Protiming valuable: it finds members who are
not in `known_members`, without downloading full participant lists.
"""

import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from .base import BaseScraper, Member, RaceResult, matches_club, matches_known_member

BASE_URL = "https://www.protiming.fr"
EVENT_HREF_RE = re.compile(r"^/events/(\d+)-[^/]+$")
# "11 septembre 2026" on the event cards, or a range "12–13 septembre 2026"
# (about one card in five) — the first day is the one we want.
DATE_RE = re.compile(
    r"\b(\d{1,2})(?:\s*[–-]\s*\d{1,2})?\s+([a-zéèûôà]+)\.?\s+(\d{4})\b",
    re.IGNORECASE,
)
# "Châlette-sur-Loing (45)" — city then department number.
LOCATION_RE = re.compile(r"^(.+?)\s*\((\d{2,3})\)$")

MONTHS_FR = {
    "janvier": "01", "fevrier": "02", "février": "02", "mars": "03",
    "avril": "04", "mai": "05", "juin": "06", "juillet": "07",
    "aout": "08", "août": "08", "septembre": "09", "octobre": "10",
    "novembre": "11", "decembre": "12", "décembre": "12",
    "janv": "01", "fev": "02", "fév": "02", "avr": "04", "juil": "07",
    "sept": "09", "oct": "10", "nov": "11", "dec": "12", "déc": "12",
}

# A runners page holds 50 rows; stop paginating well before a huge event
# drains the global scrape budget.
MAX_RUNNER_PAGES = 20


def _parse_french_date(text: str) -> str:
    """Parse "11 septembre 2026" into "2026-09-11". Empty if unparseable."""
    match = DATE_RE.search(text)
    if not match:
        return ""
    month = MONTHS_FR.get(match.group(2).lower(), "")
    if not month:
        return ""
    return f"{match.group(3)}-{month}-{match.group(1).zfill(2)}"


class ProtimingScraper(BaseScraper):
    """Scrape registered participants from Protiming event pages."""

    def __init__(self, patterns: list[str], known_members: list[str] | None = None):
        super().__init__(patterns)
        self.known_members = known_members or []

    def scrape(self, race_config: dict) -> RaceResult | None:
        url = race_config.get("url", "")
        slug = self._extract_event_slug(url)
        if not slug:
            return None

        members = self._search_by_club(slug)

        return RaceResult(
            id=f"protiming-{slug.split('-')[0]}",
            name=race_config.get("name", "Course inconnue"),
            date=race_config.get("date", ""),
            location=race_config.get("location", ""),
            platform="protiming",
            url=url,
            members=members,
            member_count=len(members),
            last_scraped=datetime.now(timezone.utc).isoformat(),
        )

    @staticmethod
    def _extract_event_slug(url: str) -> str | None:
        """Extract "{id}-{slug}" from a Protiming event URL."""
        match = re.search(r"/events/(\d+-[^/?#]+)", url)
        return match.group(1) if match else None

    def _search_by_club(self, slug: str) -> list[Member]:
        """Fetch registrants whose club matches, using the server-side filter.

        One request per search term instead of downloading the whole list.
        The server filter is a loose substring match, so every row is
        re-checked locally with the real patterns.
        """
        members: list[Member] = []
        seen: set[str] = set()

        for term in self._club_search_terms():
            for row in self._fetch_runners(slug, {"club": term}):
                name, club = row["name"], row["club"]
                if name in seen:
                    continue
                if matches_club(club, self.patterns) or matches_known_member(
                    name, self.known_members
                ):
                    seen.add(name)
                    members.append(Member(name=name, bib=row["bib"]))

        return members

    def _club_search_terms(self) -> list[str]:
        """Derive plain-text search terms from the club regex patterns.

        `run\\s*'?\\s*event\\s*86` yields both "run event 86" and
        "runevent86". Since the server filter matches substrings, only the
        shortest term of each family is kept — "run event" and "runevent"
        cover every spelling in two requests.
        """
        terms: set[str] = set()
        for pattern in self.patterns:
            for separator in (" ", ""):
                term = re.sub(r"\\s[*+]", separator, pattern)
                term = re.sub(r"[^\w\s]\?", "", term)  # optional literals like '?
                term = re.sub(r"[\\^$.*+?()\[\]{}|]", "", term)
                term = " ".join(term.split()) if separator == " " else term.strip()
                if len(term) >= 4:
                    terms.add(term.lower())
        return sorted(t for t in terms if not any(o != t and o in t for o in terms))

    def _fetch_runners(self, slug: str, params: dict) -> list[dict]:
        """Fetch and parse the runners table, following pagination."""
        rows: list[dict] = []
        for page in range(1, MAX_RUNNER_PAGES + 1):
            try:
                resp = requests.get(
                    f"{BASE_URL}/events/{slug}/runners",
                    params={**params, "page": page},
                    timeout=15,
                )
                resp.raise_for_status()
            except requests.RequestException:
                break

            page_rows = _parse_runners_table(resp.text)
            rows.extend(page_rows)
            # A short page is the last one.
            if len(page_rows) < 50:
                break
        return rows


def _parse_runners_table(html: str) -> list[dict]:
    """Parse the runners table.

    Columns: [0] Dossard, [1] Épreuve, [2] Nom / Prénom, [3] Catégorie,
    [4] Club. An empty club shows as an em dash.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Any table on the page, not just the first one: picking `select_one`
    # would silently return nothing the day a second table is inserted above
    # the runners — the very failure mode this rewrite fixes.
    for table in soup.select("table"):
        rows = []
        for tr in table.select("tr"):
            cells = tr.find_all("td")
            if len(cells) < 5:
                continue  # header row
            name = cells[2].get_text(" ", strip=True)
            if not name:
                continue
            club = cells[4].get_text(" ", strip=True)
            rows.append({
                "name": name,
                "club": "" if club in ("—", "-") else club,
                "bib": cells[1].get_text(" ", strip=True),
            })
        if rows:
            return rows
    return []


# --- Event discovery ---

def discover_races() -> list[dict]:
    """Discover all upcoming races from Protiming's event list.

    No department filter — every event is kept and checked for club members
    by the scraper, which is cheap thanks to the server-side club filter.
    """
    races: list[dict] = []
    seen: set[str] = set()

    page = 1
    while True:
        try:
            resp = requests.get(
                f"{BASE_URL}/events", params={"page": page}, timeout=15
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"  [protiming] Erreur liste page {page}: {e}")
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        new_on_page = 0
        for link in soup.select("a[href]"):
            match = EVENT_HREF_RE.match(link.get("href", "").split("?")[0])
            if not match or match.group(1) in seen:
                continue
            race = _parse_event_card(link)
            if race:
                seen.add(match.group(1))
                races.append(race)
                new_on_page += 1

        # The listing keeps serving the last page past the end.
        if not new_on_page:
            break
        page += 1

    print(f"  [protiming] {len(races)} course(s) decouverte(s)")
    return races


def _parse_event_card(link) -> dict | None:
    """Parse an event card.

    The card is one <a> holding, in order: a registration-status badge, an
    <h3> with the event name, then spans for the date ("11 septembre 2026"),
    the town ("Châlette-sur-Loing (45)") and the category. Fields are matched
    by shape rather than by position, so an extra badge cannot shift them.
    """
    name_el = link.select_one("h3")
    if not name_el:
        return None
    name = name_el.get_text(" ", strip=True)
    if len(name) < 3:
        return None

    date_str = ""
    location = ""
    for el in link.select("span, p, div"):
        if el.find(["span", "p", "div"]) is not None:
            continue  # leaf nodes only, a parent concatenates its children
        text = el.get_text(" ", strip=True)
        if not text:
            continue
        if not date_str:
            date_str = _parse_french_date(text)
        if not location:
            loc = LOCATION_RE.match(text)
            if loc:
                location = f"{loc.group(1).strip()}, {loc.group(2)}"

    return {
        "platform": "protiming",
        "url": f"{BASE_URL}{link['href'].split('?')[0]}",
        "name": name,
        "date": date_str,
        "location": location,
        "source": "protiming-discovery",
    }
