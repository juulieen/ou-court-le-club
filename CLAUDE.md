# RunEvent86

Interactive map showing races where "Run Event 86" club members are registered across France. The frontend is a static MapLibre GL map hosted via GitHub Pages (`docs/`). Data is produced by a Python scraper pipeline that discovers races on 19 registration platforms (including 4 Njuko white-labels), checks each for club members via dual matching (club name + known member names), geocodes results, and outputs a single JSON file.

## Architecture

```
config.yml              -- club patterns (4 regex), known members (22), map settings
scrapers/
  __init__.py
  main.py               -- orchestrator: discover -> scrape -> geocode -> JSON
  base.py               -- BaseScraper ABC, Member/RaceResult dataclasses,
                           matches_club(), matches_known_member(), normalize_text()
  geocoder.py            -- BAN API (primary) + Nominatim (fallback) geocoding
                           with persistent JSON cache (data/geocache.json)
  klikego.py             -- Klikego platform scraper
  njuko.py               -- Njuko platform scraper
  onsinscrit.py          -- OnSinscrit platform scraper
  protiming.py           -- Protiming platform scraper
  chronometrage.py       -- Chronometrage.com platform scraper
  chronostart.py         -- Chrono-Start platform scraper (cloudscraper)
  threewsport.py         -- 3wsport platform scraper
  espacecompetition.py   -- Espace-Competition platform scraper
  sportips.py            -- Sportips platform scraper
  timepulse.py           -- TimePulse platform scraper
  endurancechrono.py     -- Endurance Chrono platform scraper
  listino.py             -- Listino platform scraper
  runchrono.py           -- RunChrono discovery-only module (produces onsinscrit entries)
  ipitos.py              -- IPITOS platform scraper (live.ipitos.com, .clax XML)
  helloasso.py           -- HelloAsso discovery-only (participants are private)
data/
  races.json             -- final output JSON
  scrape_cache.json      -- per-URL scrape cache with TTL
  geocache.json          -- persistent geocoding cache
  njuko_slugs.json       -- persistent Njuko slug cache
docs/                    -- static frontend (GitHub Pages)
  index.html             -- MapLibre GL map with sidebar, filters, marker clusters
  js/app.js              -- map logic, fetches docs/data/races.json
  css/style.css
  data/races.json        -- anonymized version of data/races.json (no member names)
```

## Scraper Platforms

Each platform module exports a `discover_races()` function and a `*Scraper(BaseScraper)` class. Discovery finds all upcoming events nationally; the scraper then checks each event's registration list for club members using dual matching.

The `SCRAPERS` dict in `main.py` maps platform names to scraper classes (14 active scrapers). RunChrono is discovery-only (produces `onsinscrit` platform entries). Njuko also handles 4 white-label platforms (UTMB, Sporkrono, Sports107, timeto.com) via the same API.

| Platform | File | Discovery method | Scraping method | Notes |
|---|---|---|---|---|
| **Klikego** | `klikego.py` | Paginated search `/recherche?sport=0&page={N}` | AJAX POST to `findInInscrits.jsp`; searches by club name via "ville" field, falls back to known member name search | Name-based POST search returns 500 errors (broken server-side); club search via "ville" field works |
| **Njuko** | `njuko.py` | Persistent slug cache (`njuko_slugs.json`) seeded from CDX + `_SEED_SLUGS`; validates each slug via API | REST API: `/edition/url/{slug}` then `/registrations/{id}/_search/{}`, club in `metaData` keys `STRNOM_CLU`, `STRNOMABR_CLU`, `utmb_information_club` | Also handles 4 white-labels: UTMB (`register-utmb.world`), Sporkrono (`sporkrono-inscriptions.fr`), Sports107 (`sports107.com`), timeto (`timeto.com`). For large events (50k+), falls back to per-name search when bulk fetch times out |
| **OnSinscrit** | `onsinscrit.py` | National directory at `search.onsinscrit.com/evenements.php?p={page}` | HTML table at `{slug}.onsinscrit.com/listeinscrits.php?tous=1&dossards=1` | Also receives entries from RunChrono discovery |
| **Protiming** | `protiming.py` | Paginated list `/Runnings/liste/page:{N}` | HTML table `#lstParticipants` with server-side club filter via `searchclub` URL parameter; falls back to known member name search | Club filter in URL avoids downloading full participant list |
| **Chronometrage** | `chronometrage.py` | Paginated `/events` page, data in `__NEXT_DATA__` JSON | `__NEXT_DATA__` on `/eventSubscription/{slug}`, club in `observations.infoPersonne.club` | Next.js app; all data embedded in page JSON |
| **Chrono-Start** | `chronostart.py` | WP REST API `/wp-json/wp/v2/mec-events` | HTML table `#table_listing` | Cloudflare-protected; uses `cloudscraper` library to bypass |
| **3wsport** | `threewsport.py` | `/courses#allraces` filtered by department | HTML table at `/competitor/list/{eventToken}`, Club at column index 7 | Discovery filters by department for relevance |
| **Espace-Competition** | `espacecompetition.py` | Agenda page `/index.php?module=accueil&action=agenda` | HTML table at `/index.php?module=sportif&action=inscrits&comp={id}`, paginated (100/page) | Server-side pagination; must loop through all pages |
| **Sportips** | `sportips.py` | Scrape homepage for event codes | New API: `inscription.sportips.fr/api/v2/.../inscriptions/get.php?id_module={id}` with name search; old format: HTML at `sportips.fr/{CODE}/inscrits.php` | Uses `load.php` to get `id_module`, then searches by known member names via `get.php?search=` |
| **TimePulse** | `timepulse.py` | `/calendrier` page | HTML table at `/evenements/liste-epreuve/{id}/{slug}` | |
| **Endurance Chrono** | `endurancechrono.py` | Main page lists upcoming events | HTML table at `/fr/{slug}?list=part&order=club` | `order=club` URL param sorts by club for easier parsing |
| **Listino** | `listino.py` | Paginated search at `/recherche/evenement` (11 per page) | HTML table at `/slug/inscrits/{race_id}/0`, Club column | |
| **RunChrono** | `runchrono.py` | Local (dept 86) calendar at `runchrono.fr/inscription.php`; extracts OnSinscrit links from event divs | Discovery only -- produces `onsinscrit` platform entries that are scraped by `OnSinscritScraper` | No scraper class; only `discover_races()` function |
| **IPITOS** | `ipitos.py` | Index page at `live.ipitos.com/` lists events with dates in `div.nom`/`div.dt` | Wiclax `.clax` XML files extracted from iframe `?f=` param; `<E>` elements with `n`(name), `c`(club), `p`(parcours), `d`(dossard) | Uses `live.ipitos.com` (no WAF); requires browser User-Agent header; `www.ipitos.com` is blocked by Sucuri |
| **HelloAsso** | `helloasso.py` | Directory search via website (no auth needed) | N/A -- participants are private by design | `discover_races()` exists but returns `platform: manual`; members must be added manually in `config.yml` |

## Dual Matching

Club member detection uses two complementary strategies:

1. **Club matching** -- Regex patterns from `config.yml` (`club.patterns`, currently 4 patterns) are tested against the club field in each registration list. The function `matches_club()` in `base.py` normalizes accents and does case-insensitive regex matching. This catches anyone who registered under a variant of "Run Event 86".

2. **Name matching** -- A list of known member names from `config.yml` (`club.known_members`, currently 22 members) is checked against participant names. The function `matches_known_member()` in `base.py` does order-independent, accent-insensitive matching (e.g., "Jean Dupont" matches "DUPONT Jean"). This catches members who left the club field empty or filled it incorrectly.

Both strategies run on every platform. The `BaseScraper.find_club_members()` method handles club matching. Some platforms (Klikego, Protiming) use name matching as a fallback after club search yields 0 results; others check both simultaneously.

Members who neither fill the club field correctly NOR appear in the `known_members` list are invisible to the system.

## Config File Format (`config.yml`)

```yaml
club:
  patterns:            # Regex patterns to match club name in registration lists
    - "run\\s*'?\\s*event\\s*86"
    - "runevent\\s*86"
    - "runevent"
    - "run\\s*'?\\s*event"

  known_members:       # Known member names for name-based matching
    - "NOM Prenom"
    - "DUPONT Jean"
    # ... (format: "LASTNAME Firstname")

races:                 # Manual race entries (optional, overrides auto-discovery)
  # All platforms use auto-discovery (national).
  # Add manual entries here for races not found automatically,
  # especially HelloAsso races (participants not accessible by scraping).
  #
  # Example:
  # - platform: manual
  #   name: "Tic Tac Trail 2026"
  #   date: "2026-05-02"
  #   location: "Smarves, Vienne"
  #   members:
  #     - name: "NOM Prenom"
  #       bib: "7.5km"

settings:
  map_center: [46.58, 0.34]   # Leaflet map default center (lat, lng)
  map_zoom: 9                   # Leaflet map default zoom
```

## Data Flow

1. **Discovery** -- Each platform's `discover_races()` is called. Returns a list of `{platform, url, name, date, location}` dicts for all upcoming events found nationally. Typically ~4800 courses discovered across all platforms.
2. **Cache check** -- `scrape_cache.json` stores `{url: {last_scraped, member_count, data}}`. Races with no members are skipped for 48h (`CACHE_TTL_EMPTY`); races with members are re-checked every 6h (`CACHE_TTL_WITH_MEMBERS`). Cached data does NOT include lat/lng (geocoding is always redone from geocache).
3. **Scraping** -- Up to 6 concurrent threads (`ThreadPoolExecutor`, `MAX_WORKERS = 6`). Each scraper fetches the registration list and filters for club members using dual matching: `matches_club()` (regex on club name) and `matches_known_member()` (known member name matching).
4. **Enrichment** -- Each race is enriched with `race_type` (trail/route/autre) and `distances` extracted from bib fields and platform-specific structured data (e.g., Chronometrage `tourism_category.type`).
5. **Geocoding** -- Locations are geocoded via BAN API (`api-adresse.data.gouv.fr`, free, no API key, fast) as primary geocoder. Nominatim fallback for international addresses. Manual `OVERRIDES` dict in `geocoder.py` corrects known BAN errors (e.g., "Marathon" matching "Rue de Marathon"). For races without a location field, `_extract_location_from_name()` strips event-type prefixes/suffixes to extract a geocodable city name.
6. **Output** -- Two versions: `data/races.json` (full, with member names, gitignored) and `docs/data/races.json` (anonymized, no member names, public). Typically ~70 races with members found.

## Cache Files (in `data/`)

| File | Purpose |
|---|---|
| `scrape_cache.json` | Avoids re-scraping unchanged events. Keyed by URL. TTL: 48h (no members) / 6h (has members). Stores full race data for cached results with members. |
| `geocache.json` | Persistent geocoding cache. Keyed by lowercase location string. Values: `{lat, lng}` or `null` (failed lookup). |
| `njuko_slugs.json` | Persistent Njuko event slug cache. Seeded from Wayback Machine CDX, grows over time. Format: `{slugs: ["slug1", ...]}`. |
| `races.json` | Final output: `{last_updated, races: [...]}`. Each race has `id, name, date, location, platform, url, lat, lng, members[], member_count`. |

## How to Run Locally

```bash
# Install dependencies
pip install -r requirements.txt

# Run the scraper pipeline
python -m scrapers.main

# Output: data/races.json + docs/data/races.json
# Open docs/index.html in a browser to view the map
```

The full pipeline takes several minutes due to the number of platforms and rate limiting.

## Key Data Structures (in `base.py`)

- **`Member`** -- dataclass: `name: str`, `bib: str` (race/category)
- **`RaceResult`** -- dataclass: `id, name, date, location, platform, url, lat, lng, members: list[Member], member_count, last_scraped`
- **`BaseScraper`** -- ABC with `__init__(patterns, known_members)` and abstract `scrape(race_config) -> RaceResult | None`. Also provides `find_club_members(registrants) -> list[Member]` for club-field matching.
- **`matches_club(club_name, patterns)`** -- regex match against normalized club name
- **`matches_known_member(name, known_members)`** -- order-independent, accent-insensitive name matching
- **`normalize_text(text)`** -- strips accents (NFD decomposition) and whitespace

## How to Add a New Scraper

1. Create `scrapers/<platform>.py` with:
   - A `discover_races() -> list[dict]` function that returns `{platform, url, name, date, location}` dicts
   - A `*Scraper(BaseScraper)` class implementing `scrape(race_config) -> RaceResult | None`
   - Use `matches_club(club_name, self.patterns)` from `base.py` to check club membership
   - Use `matches_known_member(name, self.known_members)` for name-based matching
2. In `scrapers/main.py`:
   - Import the scraper class and discover function
   - Add the scraper class to the `SCRAPERS` dict (keyed by platform name string)
   - Add a `discover_races()` call in `run()` alongside the others
3. All scrapers receive `known_members` via `__init__` -- dual matching is the default for all platforms.

## Known Limitations

- **IPITOS** uses `live.ipitos.com` to bypass the Sucuri WAF on `www.ipitos.com`. Requires browser User-Agent header. If the live subdomain structure changes, discovery and scraping will break.
- **HelloAsso** participants are private by design; no scraper possible. `helloasso.py` has a `discover_races()` function but participants must be added manually in `config.yml`.
- **Njuko** discovery depends on a persistent slug cache (`njuko_slugs.json`). CDX seeding from Wayback Machine is slow and unreliable. Club-relevant slugs can be added to `_SEED_SLUGS` in `njuko.py`. White-label platforms (UTMB, Sporkrono, Sports107, timeto) share the same Njuko API.
- **Large events** (50k+ participants like Marathon de Paris on timeto.com) -- bulk registration fetch times out; falls back to per-name search of known members. This means club-field-only matches (members not in `known_members`) are missed on very large events.
- **Name matching false positives** -- On large events, common names in `known_members` may match unrelated people (homonymes). No mitigation currently.
- **Past events** -- Discovery only finds upcoming events. Once a race passes and the platform disables its participant list, the race becomes invisible unless added manually in `config.yml`.
- **Chrono-Start** requires `cloudscraper` to bypass Cloudflare protection; may break if Cloudflare changes detection.
- **Klikego** name-based POST search is broken (server returns 500 errors). Club search via "ville" field works but members who don't fill the club field are missed unless in `known_members`.
- **Invisible members** -- Members who neither fill the club field correctly NOR appear in the `known_members` list in `config.yml` are completely invisible to the system.
- **Geocoding** -- BAN API sometimes matches race names to street names (e.g., "Marathon" → "Rue de Marathon"). Manual `OVERRIDES` dict in `geocoder.py` corrects known errors. Nominatim fallback uses free tier with 1 req/sec rate limit.
- **No incremental updates** -- each run re-discovers all events nationally. The scrape cache mitigates this but discovery itself still runs every time.
- **Date handling** -- Klikego dates assume the current year (no year in the source data), which may be incorrect near year boundaries.

## Dev Workflow (Local vs CI)

**Always test locally first** before pushing to CI. Use the cache CLI:

```bash
# Sync CI cache to local
python -m scrapers.cache_cli sync pull

# Clear specific entries and re-test
python -m scrapers.cache_cli clear -u "marathon-paris"
python -m scrapers.main

# When satisfied, push to CI
python -m scrapers.cache_cli ci run --fresh
```

Key commands:
- `python -m scrapers.cache_cli stats` — overview of local cache
- `python -m scrapers.cache_cli list -m` — races with members
- `python -m scrapers.cache_cli clear -p <platform>` — clear platform cache
- `python -m scrapers.cache_cli sync diff` — compare local vs CI
- `python -m scrapers.cache_cli ci run --fresh` — clear CI cache + trigger run

**Important:** Local and CI caches are independent. Code-level fixes (OVERRIDES in geocoder.py, _SEED_SLUGS in njuko.py) propagate automatically. Cache-level fixes (scrape_cache entries) do NOT sync — use `ci run --fresh` to rebuild CI cache.

## Njuko White-Labels

Several platforms are Njuko white-labels sharing the same API structure. Supported via `_API_BASES` dict and `_extract_slug()` in `njuko.py`:

| Domain | API Base | Events |
|---|---|---|
| `njuko.com` (default) | `front-api.njuko.com` | Most races |
| `register-utmb.world` | `front-api.njuko.com` | UTMB World Series |
| `sporkrono-inscriptions.fr` | `front-api.sporkrono-inscriptions.fr` | L'Épopée Royale |
| `sports107.com` | `front-api.sports107.com` | SaintéLyon |
| `timeto.com` | `front-api.timeto.com` | Marathon de Paris (ASO) |

To add a new white-label: add domain to `_API_BASES`, `_extract_slug()`, and optionally `_SEED_SLUGS`.

**Large events (50k+):** Bulk `_search/{}` times out. NjukoScraper falls back to per-name search of known members. This means club-only matches are missed on very large events.

## Geocoding Gotchas

- **BAN API matches race names to street names** (e.g., "marathon" → "Rue de Marathon" in Rennes). Use `OVERRIDES` dict in `geocoder.py` for manual corrections.
- **Geocoding pipeline:** OVERRIDES > geocache > BAN API > Nominatim. OVERRIDES are in code and always win.
- **Race name extraction:** `_extract_location_from_name()` in `main.py` strips event-type prefixes/suffixes to extract city names.
- **Scrape cache does NOT store lat/lng** — geocoding is always redone from geocache to allow corrections.
- **Cache bust:** Increment `?v=N` in `docs/index.html` CSS/JS links after frontend changes.
