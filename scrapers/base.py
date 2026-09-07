"""Base scraper class and data structures."""

import re
import unicodedata
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class Member:
    name: str
    bib: str = ""


@dataclass
class RaceResult:
    id: str
    name: str
    date: str
    location: str
    platform: str
    url: str = ""
    lat: float | None = None
    lng: float | None = None
    members: list[Member] = field(default_factory=list)
    member_count: int = 0
    last_scraped: str = ""
    # Optional metadata a platform can provide directly (e.g. IPITOS .clax
    # headers). Left empty when the platform exposes nothing; main._enrich_race
    # then falls back to detecting them from the race name and bibs.
    race_type: str = ""
    distances: list[float] = field(default_factory=list)


def normalize_text(text: str) -> str:
    """Remove accents and normalize whitespace for matching."""
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return text.strip()


def matches_club(club_name: str, patterns: list[str]) -> bool:
    """Check if a club name matches any of the configured patterns."""
    normalized = normalize_text(club_name).lower()
    return any(re.search(pattern, normalized, re.IGNORECASE) for pattern in patterns)


def _match_known_member(name: str, known_members: list[str]) -> str | None:
    """Return the config.yml entry matching a participant name, else None.

    Matching is order-independent and accent-insensitive:
    "Jean Dupont" matches "DUPONT Jean", "Dupont Jean", etc.
    """
    if not name or not known_members:
        return None
    name_parts = set(normalize_text(name).lower().split())
    if len(name_parts) < 2:
        return None
    for member in known_members:
        member_parts = set(normalize_text(member).lower().split())
        if len(member_parts) < 2:
            continue
        # All parts of the shorter name must appear in the longer
        shorter, longer = (name_parts, member_parts) if len(name_parts) <= len(member_parts) else (member_parts, name_parts)
        if shorter.issubset(longer):
            return member
    return None


def matches_known_member(name: str, known_members: list[str]) -> bool:
    """Check if a participant name matches any known club member."""
    return _match_known_member(name, known_members) is not None


def canonical_member_name(name: str, known_members: list[str]) -> str:
    """Fold a participant name onto its config.yml spelling.

    Platforms spell the same runner in incompatible ways — "ROMAIN RICHARD",
    "Romain RICHARD", "RICHARD Romain", or with a non-breaking space between the
    two parts — which inflates the runner count and splits the per-member filter
    into several entries.
    Names that match no known member are returned with whitespace normalized
    (non-breaking spaces included), so they at least dedupe against themselves.
    """
    cleaned = " ".join(name.split())
    return _match_known_member(cleaned, known_members) or cleaned


class BaseScraper(ABC):
    """Abstract base class for platform scrapers."""

    def __init__(self, patterns: list[str], known_members: list[str] | None = None):
        self.patterns = patterns
        self.known_members = known_members or []

    @abstractmethod
    def scrape(self, race_config: dict) -> RaceResult | None:
        """Scrape a race page and return results."""
        ...

    def find_club_members(self, registrants: list[dict]) -> list[Member]:
        """Filter registrants to find club members.

        Each registrant dict should have at least 'name' and 'club' keys,
        and optionally 'bib'.
        """
        members = []
        for reg in registrants:
            club = reg.get("club", "")
            if club and matches_club(club, self.patterns):
                members.append(Member(
                    name=reg.get("name", "Inconnu"),
                    bib=reg.get("bib", ""),
                ))
        return members
