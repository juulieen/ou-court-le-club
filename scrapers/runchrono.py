"""RunChrono discovery scraper.

RunChrono (www.runchrono.fr) is a local timing company for dept 86 (Vienne).
Their inscription.php page lists upcoming races with OnSinscrit registration links.

Page structure: events are nested divs with id="YYYYMMDD_Event_Name".
Each event has direct children:
  <h1>Event Name (dept)</h1>
  <h5>Day DD Month at Xh</h5>
  <div class="container"> ... <a href="https://slug.onsinscrit.com"> ... </div>
"""

import re

import requests
from bs4 import BeautifulSoup, Tag


CALENDAR_URL = "https://www.runchrono.fr/inscription.php"


def discover_races() -> list[dict]:
    """Scrape RunChrono's inscription.php to discover upcoming races.

    Returns a list of race configs compatible with the main orchestrator.
    """
    try:
        resp = requests.get(CALENDAR_URL, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  [runchrono] Erreur chargement calendrier: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    races = []
    seen_urls = set()

    # Find all event divs by their ID pattern: YYYYMMDD_EventName
    for div in soup.find_all("div", id=re.compile(r"^\d{8}_")):
        race = _parse_event_div(div)
        if race and race["url"] not in seen_urls:
            races.append(race)
            seen_urls.add(race["url"])

    print(f"  [runchrono] {len(races)} course(s) decouverte(s)")
    return races


def _parse_event_div(div: Tag) -> dict | None:
    """Parse a single event div to extract race info."""
    div_id = div.get("id", "")

    # Extract date from ID: 20260322_Nieuil_Lespoir -> 2026-03-22
    date_match = re.match(r"(\d{4})(\d{2})(\d{2})_(.+)", div_id)
    if not date_match:
        return None

    year, month, day = date_match.group(1), date_match.group(2), date_match.group(3)
    date_str = f"{year}-{month}-{day}"
    slug_from_id = date_match.group(4).replace("_", " ")

    # Get event name from direct child <h1> and subtitle from <h5>
    name = ""
    h5_text = ""
    for child in div.children:
        if isinstance(child, Tag):
            if child.name == "h1" and not name:
                name = child.get_text(strip=True)
            elif child.name == "h5" and not h5_text:
                h5_text = child.get_text(strip=True)

    # Extract location from name/subtitle/slug
    location = _extract_location(name, h5_text, slug_from_id)

    # Clean up name (remove dept number suffix)
    if name:
        name = re.sub(r"\s*\(\d{2,3}\)\s*$", "", name).strip()
    else:
        name = slug_from_id.title()

    # Find OnSinscrit link in direct child container only
    onsinscrit_url = None
    for child in div.children:
        if isinstance(child, Tag) and "container" in (child.get("class") or []):
            for link in child.find_all("a", href=re.compile(r"onsinscrit\.com")):
                onsinscrit_url = link["href"].strip()
                break
            break

    if not onsinscrit_url:
        return None

    if not onsinscrit_url.startswith("http"):
        onsinscrit_url = "https://" + onsinscrit_url

    return {
        "platform": "onsinscrit",
        "url": onsinscrit_url,
        "name": name,
        "date": date_str,
        "location": location,
        "source": "runchrono",
    }


def _extract_location(name: str, h5_text: str, slug_fallback: str) -> str:
    """Extract city/location from event name, h5 subtitle, or slug.

    Strategies (in order):
    1. Look for "à CityName" in h1 name text
    2. Look for city in h5 text (often "Dimanche 22 Mars à 9h30 - CityName")
    3. Fall back to slug (replace underscores, clean up)
    """
    # Get department from name if present: "(86)"
    dept = ""
    dept_match = re.search(r"\((\d{2,3})\)", name)
    if dept_match:
        dept_num = dept_match.group(1)
        dept = f", {_dept_name(dept_num)}" if _dept_name(dept_num) else ""

    # Strategy 1: "à CityName" or "a CityName" or "de CityName" in event name
    city_match = re.search(r"\b(?:à|a)\s+(.+?)(?:\s*\(\d{2,3}\))?$", name, re.IGNORECASE)
    if city_match:
        city = city_match.group(1).strip()
        if len(city) > 2:
            return f"{city}{dept}"

    # Strategy 1b: "de CityName" pattern (e.g., "Trail de la Fee Melusine de Jaunay Marigny")
    # or "Les Foulées de Chab" -> try last "de X" if X looks like a city
    de_match = re.search(r"\bde\s+([A-Z][a-zA-Zéèêëàâùûôïîç\s-]+?)(?:\s*\d{4})?(?:\s*\(\d{2,3}\))?$", name)
    if de_match:
        city = de_match.group(1).strip()
        # Only use if it looks like a place name (starts with uppercase, not too long)
        if 2 < len(city) < 40 and city[0].isupper():
            return f"{city}{dept}"

    # Strategy 2: city in h5 subtitle
    if h5_text:
        # Sometimes format: "Dimanche 22 Mars à 9h30" or with location appended
        h5_city = re.search(r"[-–]\s*(.+)$", h5_text)
        if h5_city:
            city = h5_city.group(1).strip()
            if len(city) > 2:
                return f"{city}{dept}"

    # Strategy 3: slug fallback - clean common prefixes
    city = slug_fallback.strip()
    # Remove common event-type prefixes from slug
    for prefix in ("Course ", "Trail ", "Semi ", "Foulees ", "Fouleesde"):
        if city.lower().startswith(prefix.lower()):
            city = city[len(prefix):]
    city = city.strip()
    return f"{city}{dept}" if city else ""


def _dept_name(num: str) -> str:
    """Map department number to name for common local departments."""
    depts = {
        "86": "Vienne",
        "79": "Deux-Sevres",
        "37": "Indre-et-Loire",
        "87": "Haute-Vienne",
        "36": "Indre",
        "16": "Charente",
        "17": "Charente-Maritime",
        "49": "Maine-et-Loire",
        "91": "Essonne",
    }
    return depts.get(num, "")
