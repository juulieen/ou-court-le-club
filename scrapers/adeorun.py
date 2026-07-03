"""Scraper for Adeorun (adeorun.com).

Adeorun is a multi-tenant Next.js SPA: the national calendar lives on
``calendrier.adeorun.com`` and each event has its own ``{subdomain}.adeorun.com``.
Two public JSON endpoints (``/api/public/*``, no auth, no WAF) provide everything.

- Discovery: ``calendrier.adeorun.com/api/public/calendar2?page={N}`` — 20 events
  per page, upcoming only. Each event has ``subdomain``, ``title``, ``day`` (UNIX
  seconds), ``zip_city`` ("VILLE (DEPT)"), ``tags[]`` (disciplines).
- Participant list: needs the event's ``eventId`` (cuid), obtained by parsing the
  ``__NEXT_DATA__`` of ``{subdomain}.adeorun.com/participants``. Then
  ``{subdomain}.adeorun.com/api/public/participants2?eventId={id}&page={N}&limit=20``
  returns participants with ``name`` and ``team`` (public club field).

Club field ``team`` is public → full dual matching applies (matches_club on
``team`` + matches_known_member on ``name``).
"""

import json
import math
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from .base import BaseScraper, Member, RaceResult, matches_club, matches_known_member

CALENDAR_URL = "https://calendrier.adeorun.com/api/public/calendar2"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9",
}

# Discipline tags (event.tags[].title) that count as running/trail.
_RUNNING_TAGS = {"trail", "course", "cross", "urban trail", "course à pied"}

_PAGE_SIZE = 20              # server hard-caps participants2 limit at 20
_MAX_DISCOVERY_PAGES = 30    # safety cap (~600 events)
_MAX_PARTICIPANT_PAGES = 60  # safety cap (~1200 registrants downloaded per event)
_BIG_EVENT_THRESHOLD = 800   # above this, search by known-member name (politeness)


def _is_running(event: dict) -> bool:
    """Return True if the event has a running/trail discipline tag."""
    for tag in event.get("tags") or []:
        title = (tag.get("title") or "").strip().lower()
        if title in _RUNNING_TAGS:
            return True
    return False


def discover_races() -> list[dict]:
    """Return upcoming running events on Adeorun (national calendar)."""
    races = []
    seen_subdomains = set()
    today = datetime.now().strftime("%Y-%m-%d")

    for page in range(1, _MAX_DISCOVERY_PAGES + 1):
        try:
            resp = requests.get(
                CALENDAR_URL,
                params={"page": page, "q": "", "discipline": "",
                        "startAt": "", "endAt": "", "city": "", "distanceKm": ""},
                headers=_HEADERS,
                timeout=15,
            )
            resp.raise_for_status()
            events = resp.json().get("data", [])
        except (requests.RequestException, ValueError) as e:
            print(f"  [adeorun] Erreur calendrier page {page}: {e}")
            break

        if not events:
            break

        for e in events:
            if not _is_running(e):
                continue
            subdomain = (e.get("subdomain") or "").strip()
            if not subdomain or subdomain in seen_subdomains:
                continue

            day = e.get("day")
            try:
                date = datetime.fromtimestamp(float(day), tz=timezone.utc).strftime("%Y-%m-%d")
            except (TypeError, ValueError, OSError):
                continue
            if date < today:
                continue

            seen_subdomains.add(subdomain)
            races.append({
                "platform": "adeorun",
                "url": f"https://{subdomain}.adeorun.com",
                "name": (e.get("title") or "").strip(),
                "date": date,
                "location": (e.get("zip_city") or "").strip(),
            })

        if len(events) < _PAGE_SIZE:
            break

    print(f"  [adeorun] {len(races)} course(s) decouverte(s)")
    return races


class AdeorunScraper(BaseScraper):
    """Scrape registered participants from Adeorun event subdomains."""

    def scrape(self, race_config: dict) -> RaceResult | None:
        url = race_config.get("url", "")
        name = race_config.get("name", "Course inconnue")
        date = race_config.get("date", "")
        location = race_config.get("location", "")

        subdomain = self._extract_subdomain(url)
        if not subdomain:
            return None

        event_id = self._get_event_id(subdomain)
        if not event_id:
            return None

        # The event page's ``pCount`` is unreliable (often 0 despite registrants),
        # so the authoritative count comes from participants2's ``total_hits``.
        first_page, total = self._fetch_participants_page(subdomain, event_id, 1)

        if total == 0 and not first_page:
            members: list[Member] = []
        elif total > _BIG_EVENT_THRESHOLD:
            # Large event — don't download everything; search by known member name.
            members = self._search_by_names(subdomain, event_id)
        else:
            members = self._bulk_members(subdomain, event_id, total, first_page)

        return RaceResult(
            id=f"adeorun-{subdomain}",
            name=name,
            date=date,
            location=location,
            platform="adeorun",
            url=url,
            members=members,
            member_count=len(members),
            last_scraped=datetime.now(timezone.utc).isoformat(),
        )

    @staticmethod
    def _extract_subdomain(url: str) -> str | None:
        """Extract the event subdomain from https://{subdomain}.adeorun.com[/...]."""
        if "adeorun.com" not in url:
            return None
        host = url.split("//", 1)[-1].split("/", 1)[0]
        sub = host.split(".adeorun.com", 1)[0]
        # Ignore the calendar host itself
        if not sub or sub in ("www", "calendrier"):
            return None
        return sub

    def _get_event_id(self, subdomain: str) -> str | None:
        """Return the event's cuid, parsed from the /participants page __NEXT_DATA__."""
        try:
            resp = requests.get(
                f"https://{subdomain}.adeorun.com/participants",
                headers=_HEADERS, timeout=20,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"  [adeorun] Erreur page participants ({subdomain}): {e}")
            return None

        soup = BeautifulSoup(resp.text, "html.parser")
        script = soup.select_one("script#__NEXT_DATA__")
        if not script:
            return None
        try:
            data = json.loads(script.string)
            return data["props"]["pageProps"]["eventInfo"]["data"].get("eventId")
        except (json.JSONDecodeError, TypeError, KeyError):
            return None

    def _fetch_participants_page(self, subdomain: str, event_id: str,
                                 page: int, q: str = "") -> tuple[list[dict], int]:
        """Fetch one page of participants2. Returns (participants, total_hits)."""
        try:
            resp = requests.get(
                f"https://{subdomain}.adeorun.com/api/public/participants2",
                params={"eventId": event_id, "q": q, "page": page,
                        "limit": _PAGE_SIZE, "item": ""},
                headers=_HEADERS, timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError):
            return [], 0
        return data.get("data", []) or [], data.get("total_hits", 0) or 0

    def _bulk_members(self, subdomain: str, event_id: str, total: int,
                      first_page: list[dict]) -> list[Member]:
        """Download all participant pages and dual-match (club field + known name).

        ``first_page`` (page 1, already fetched to learn ``total``) is reused so we
        don't request it twice.
        """
        pages = min(math.ceil(total / _PAGE_SIZE), _MAX_PARTICIPANT_PAGES)
        members: list[Member] = []
        seen: set[str] = set()
        members.extend(self._match(first_page, seen))
        for page in range(2, pages + 1):
            participants, _ = self._fetch_participants_page(subdomain, event_id, page)
            if not participants:
                break
            members.extend(self._match(participants, seen))
        return members

    def _search_by_names(self, subdomain: str, event_id: str) -> list[Member]:
        """For big events: one search per known member (name-only, club-only missed)."""
        members: list[Member] = []
        seen: set[str] = set()
        for full_name in (self.known_members or []):
            parts = full_name.strip().split()
            if not parts:
                continue
            last_name = parts[0] if parts[0].isupper() else parts[-1]
            participants, _ = self._fetch_participants_page(
                subdomain, event_id, page=1, q=last_name)
            for m in self._match(participants, seen):
                members.append(m)
        return members

    def _match(self, participants: list[dict], seen: set[str]):
        """Yield Members from a participant list via dual matching."""
        for p in participants:
            full_name = (p.get("name") or "").strip()
            team = (p.get("team") or "").strip()
            if not full_name or full_name.lower() in seen:
                continue

            is_club = team and matches_club(team, self.patterns)
            is_name = matches_known_member(full_name, self.known_members)
            if not is_club and not is_name:
                continue

            seen.add(full_name.lower())
            bib = (p.get("parcours") or p.get("category") or "").strip()
            yield Member(name=full_name, bib=bib)
