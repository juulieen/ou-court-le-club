"""Scraper for chronometrage.com (DAG System).

Next.js SSR site — all data is in __NEXT_DATA__ JSON embedded in HTML.
- Event list: /events (paginated, all in __NEXT_DATA__)
- Registration list: /eventSubscription/{slug}
- Club info in: subscriptions[].observations.infoPersonne.club
"""

import json
import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from .base import BaseScraper, Member, RaceResult, matches_club, matches_known_member


class ChronometrageScraper(BaseScraper):
    """Scrape participants from chronometrage.com via __NEXT_DATA__ JSON."""

    BASE = "https://www.chronometrage.com"

    def scrape(self, race_config: dict) -> RaceResult | None:
        url = race_config.get("url", "")
        name = race_config.get("name", "Course inconnue")
        date = race_config.get("date", "")
        location = race_config.get("location", "")

        slug = self._extract_slug(url)
        if not slug:
            return None

        sub_url = f"{self.BASE}/eventSubscription/{slug}"
        next_data = _fetch_next_data(sub_url)
        if not next_data:
            return None

        members = self._parse_subscriptions(next_data)

        return RaceResult(
            id=f"chronometrage-{slug}",
            name=name,
            date=date,
            location=location,
            platform="chronometrage",
            url=url,
            members=members,
            member_count=len(members),
            last_scraped=datetime.now(timezone.utc).isoformat(),
        )

    def _extract_slug(self, url: str) -> str | None:
        match = re.search(r"chronometrage\.com/\w+/([^/?]+)", url)
        if match:
            return match.group(1)
        # Might be just a slug
        if "/" not in url and url:
            return url
        return None

    def _parse_subscriptions(self, next_data: dict) -> list[Member]:
        """Extract club members from __NEXT_DATA__ subscription data."""
        members = []
        seen = set()

        # Navigate: props.pageProps.initialData or dehydratedState
        initial = (
            next_data.get("props", {})
            .get("pageProps", {})
            .get("initialData", {})
        )

        # initialData can be a dict with challenges, or a list
        challenges = []
        if isinstance(initial, dict):
            challenges = initial.get("challenges", [])
            if not challenges:
                # Try flat subscriptions
                subs = initial.get("subscriptions", [])
                if subs:
                    challenges = [{"subscriptions": subs}]
        elif isinstance(initial, list):
            challenges = [{"subscriptions": initial}]

        for challenge in challenges:
            race_name = challenge.get("name", "")
            for sub in challenge.get("subscriptions", []):
                club = self._extract_club(sub)
                firstname = sub.get("firstname", "")
                lastname = sub.get("lastname", "")
                name = f"{firstname} {lastname}".strip()

                is_club = club and matches_club(club, self.patterns)
                is_name = matches_known_member(name, self.known_members)
                if not is_club and not is_name:
                    continue

                if not name or name in seen:
                    continue
                seen.add(name)

                bib = race_name or str(sub.get("bib", ""))
                members.append(Member(name=name, bib=bib))

        return members

    def _extract_club(self, sub: dict) -> str:
        """Extract club from subscription's observations.infoPersonne."""
        obs_raw = sub.get("observations", "")
        if not obs_raw:
            return ""

        try:
            if isinstance(obs_raw, str):
                obs = json.loads(obs_raw)
            else:
                obs = obs_raw
        except (json.JSONDecodeError, TypeError):
            return ""

        info = obs.get("infoPersonne", {})
        if isinstance(info, dict):
            return info.get("club", "") or info.get("team", "")
        return ""


# --- Event discovery ---

# Map chronometrage.com tourism_category.type values to our race_type taxonomy
_TOURISM_TYPE_MAP = {
    "TRAIL": "trail",
    "RUNNING": "route",
    "TRIATHLON": "triathlon",
    "MARCHE": "marche",
    "RAID": "trail",
    "CANICROSS": "trail",
}


def _map_tourism_type(raw_type: str) -> str:
    """Map a chronometrage.com tourism_category.type to our race_type.

    Returns empty string if the type is unknown or empty (caller should
    fall back to text-based detection).
    """
    if not raw_type:
        return ""
    return _TOURISM_TYPE_MAP.get(raw_type.upper().strip(), "")


def discover_races() -> list[dict]:
    """Discover events from chronometrage.com via __NEXT_DATA__.

    The first page has 20 events. We also fetch subsequent pages
    by incrementing the page parameter.
    """
    all_events = []

    # Fetch first page to get total count and initial events
    next_data = _fetch_next_data("https://www.chronometrage.com/events")
    if not next_data:
        print("  [chronometrage] Erreur chargement page events")
        return []

    events = _extract_events(next_data)
    all_events.extend(events)

    # Get total count and fetch remaining pages
    pages_data = (
        next_data.get("props", {})
        .get("pageProps", {})
        .get("initialEvents", {})
        .get("pages", [{}])
    )
    if pages_data:
        total = pages_data[0].get("count", 0)
        per_page = len(events) or 20
        page = 2
        while len(all_events) < total:
            nd = _fetch_next_data(f"https://www.chronometrage.com/events?page={page}")
            if not nd:
                break
            more = _extract_events(nd)
            if not more:
                break
            all_events.extend(more)
            page += 1

    races = []
    for ev in all_events:
        slug = ev.get("slug", "")
        if not slug:
            continue

        name = ev.get("name", slug)
        city = ev.get("city", "")
        region = ev.get("region", "")
        location = f"{city}, {region}" if city and region else city or region

        start_date = ev.get("start_date", "") or ev.get("startDate", "")
        date_str = ""
        if start_date:
            dm = re.match(r"(\d{4}-\d{2}-\d{2})", start_date)
            if dm:
                date_str = dm.group(1)

        # Extract structured race type from tourism_category.type
        # Values seen: "TRAIL", "RUNNING", "TRIATHLON", etc.
        race_type = _map_tourism_type(
            ev.get("tourism_category", {}).get("type", "")
            if isinstance(ev.get("tourism_category"), dict)
            else ""
        )

        entry = {
            "platform": "chronometrage",
            "url": f"https://www.chronometrage.com/eventSubscription/{slug}",
            "name": name,
            "date": date_str,
            "location": location,
            "source": "chronometrage-discovery",
        }
        if race_type:
            entry["race_type"] = race_type

        races.append(entry)

    print(f"  [chronometrage] {len(races)} course(s) decouverte(s)")
    return races


def _fetch_next_data(url: str) -> dict | None:
    """Fetch a Next.js page and extract __NEXT_DATA__ JSON."""
    try:
        resp = requests.get(url, timeout=10)
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


def _extract_events(next_data: dict) -> list[dict]:
    """Extract event list from __NEXT_DATA__.

    Structure: props.pageProps.initialEvents.pages[].result[]
    """
    props = next_data.get("props", {}).get("pageProps", {})

    initial = props.get("initialEvents", {})
    if isinstance(initial, dict):
        pages = initial.get("pages", [])
        events = []
        for page in pages:
            events.extend(page.get("result", []))
        return events

    # Fallback: try direct events
    events = props.get("events", [])
    if events:
        return events

    return []
