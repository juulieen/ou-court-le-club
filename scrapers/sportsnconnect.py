"""Scraper for Sports'N Connect (sportsnconnect.com).

Next.js SSR site — all data is embedded in __NEXT_DATA__ JSON in the HTML.
- Discovery: /fr/client/events loads all ~460 events at once in pageProps.initialEditions
- Participant list: /fr/client/events/{uuid}-{slug} embeds ALL participants in
  pageProps.initialEdition.participants (no API auth needed)
- Club info: contact.club
- Name: contact.firstname + contact.lastname
"""

import json
import re
import unicodedata
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from .base import BaseScraper, Member, RaceResult, matches_club, matches_known_member

DISCOVERY_URL = "https://new.sportsnconnect.com/fr/client/events"
EVENT_BASE = "https://new.sportsnconnect.com/fr/client/events"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9",
}


def _slugify(text: str) -> str:
    """Convert text to a URL-safe slug (lowercase, accents stripped, hyphens)."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def _fetch_next_data(url: str, timeout: int = 30) -> dict | None:
    """Fetch a Next.js page and extract its embedded __NEXT_DATA__ JSON."""
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    script = soup.select_one("script#__NEXT_DATA__")
    if not script:
        return None
    try:
        return json.loads(script.string)
    except (json.JSONDecodeError, TypeError):
        return None


def _is_running(edition: dict) -> bool:
    """Return True if this edition is a running/trail event."""
    event = edition.get("event") or {}
    sports = event.get("sports") or []
    sport_names = {s.get("name", "").strip() for s in sports}
    if any("course à pied" in s.lower() for s in sport_names):
        return True
    # Some events have no sport tag — use sport categories as fallback
    cats = event.get("sportCategories") or []
    cat_names = " ".join(c.get("name", "") for c in cats).lower()
    if any(kw in cat_names for kw in ("trail", "route", "course", "run", "marathon")):
        return True
    return False


def discover_races() -> list[dict]:
    """Return upcoming running events on Sports'N Connect."""
    data = _fetch_next_data(DISCOVERY_URL)
    if not data:
        print("  [sportsnconnect] Erreur chargement page discovery")
        return []

    editions = data.get("props", {}).get("pageProps", {}).get("initialEditions", [])
    if isinstance(editions, dict):
        editions = editions.get("hydra:member", [])

    today = datetime.now().strftime("%Y-%m-%d")
    races = []
    for e in editions:
        # Skip non-running events
        if not _is_running(e):
            continue

        start_date = (e.get("startDate") or "")[:10]
        if start_date < today:
            continue

        uuid = e.get("uuid", "")
        if not uuid:
            continue

        intro = e.get("intro", "") or ""
        slug = _slugify(intro)
        city = e.get("city", "")
        department = e.get("department", "")
        location = f"{city}, {department}" if city and department else city or department

        races.append({
            "platform": "sportsnconnect",
            "url": f"{EVENT_BASE}/{uuid}-{slug}",
            "name": intro,
            "date": start_date,
            "location": location,
        })

    print(f"  [sportsnconnect] {len(races)} course(s) decouverte(s)")
    return races


class SportsnConnectScraper(BaseScraper):
    """Scrape participants from Sports'N Connect via __NEXT_DATA__."""

    def scrape(self, race_config: dict) -> RaceResult | None:
        url = race_config.get("url", "")
        name = race_config.get("name", "Course inconnue")
        date = race_config.get("date", "")
        location = race_config.get("location", "")

        # The HTML can be large (4000+ participants) — allow extra time
        data = _fetch_next_data(url, timeout=60)
        if not data:
            return None

        edition = data.get("props", {}).get("pageProps", {}).get("initialEdition")
        if not edition:
            return None

        participants = edition.get("participants", [])
        if isinstance(participants, dict):
            participants = participants.get("hydra:member", [])

        uuid = edition.get("uuid", "")
        members = []
        seen: set[str] = set()

        for p in participants:
            if p.get("isAnonymous"):
                continue

            contact = p.get("contact") or {}
            firstname = (contact.get("firstname") or "").strip()
            lastname = (contact.get("lastname") or "").strip()
            club = (contact.get("club") or "").strip()
            full_name = f"{firstname} {lastname}".strip()

            if not full_name or full_name in seen:
                continue

            is_club = club and matches_club(club, self.patterns)
            is_name = matches_known_member(full_name, self.known_members)
            if not is_club and not is_name:
                continue

            seen.add(full_name)
            trial = (
                p.get("orderParticipant", {})
                .get("orderTrial", {})
                .get("trial", {})
                .get("title", "")
            )
            members.append(Member(name=full_name, bib=trial))

        return RaceResult(
            id=f"sportsnconnect-{uuid}",
            name=name,
            date=date,
            location=location,
            platform="sportsnconnect",
            url=url,
            members=members,
            member_count=len(members),
            last_scraped=datetime.now(timezone.utc).isoformat(),
        )
