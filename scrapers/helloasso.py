"""HelloAsso directory discovery (no scraping of participants).

HelloAsso does NOT expose participant lists publicly. This module only
discovers running events via the public directory search, so we know
which races exist on HelloAsso. Members can then be added manually
in config.yml if they know they registered on HelloAsso.

Directory search: POST /v5/directory/forms (requires OAuth2, but the
website search at helloasso.com works without auth via Nuxt SSR).
"""

import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.helloasso.com"


def discover_races() -> list[dict]:
    """Discover running events on HelloAsso via website search.

    Searches for running/trail events near Vienne (86) and surrounding
    departments. Returns race configs with platform='manual' since
    HelloAsso participants can't be scraped — they must be added manually.
    """
    races = []
    seen = set()

    # Search for running events in nearby cities
    queries = [
        "course à pied Poitiers",
        "trail Vienne 86",
        "course trail Chatellerault",
        "marathon Poitiers",
    ]

    for query in queries:
        found = _search_events(query)
        for race in found:
            url = race.get("url", "")
            if url not in seen:
                races.append(race)
                seen.add(url)

    print(f"  [helloasso] {len(races)} evenement(s) decouverts (participants non accessibles)")
    return races


def _search_events(query: str) -> list[dict]:
    """Search HelloAsso website for events."""
    results = []
    try:
        resp = requests.get(
            f"{BASE_URL}/e/recherche",
            params={"query": query, "type": "EVENT"},
            timeout=10,
        )
        resp.raise_for_status()
    except requests.RequestException:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")

    # Extract event links from search results
    for link in soup.select("a[href*='/associations/'][href*='/evenements/']"):
        href = link.get("href", "")
        if not href or href in {r.get("url") for r in results}:
            continue

        full_url = href if href.startswith("http") else f"{BASE_URL}{href}"
        name = link.get_text(strip=True)
        if not name or len(name) < 5:
            continue

        results.append({
            "platform": "manual",  # Can't scrape participants
            "url": full_url,
            "name": f"[HelloAsso] {name}",
            "date": "",
            "location": "",
            "source": "helloasso-discovery",
            "_helloasso": True,
        })

    return results
