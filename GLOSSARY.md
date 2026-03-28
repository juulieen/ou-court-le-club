# RunEvent86 Glossary

Project-specific terms and concepts used throughout the codebase.

## Platform vs Source

- **Platform**: A registration website where runners sign up for races (e.g., Klikego, Njuko, Protiming). Each platform has its own scraper module in `scrapers/`. The `platform` field in a race config identifies which scraper handles it.
- **Source**: Where a race entry came from -- either auto-discovered by a platform's `discover_races()` function, or manually added in `config.yml`. A single platform can be both a discovery source and a scraping target.

## Discovery

The process of finding upcoming race events on a platform. Each platform module exports a `discover_races()` function that crawls the platform's event listing pages and returns a list of race config dicts (`{platform, url, name, date, location}`). Discovery runs nationally across all of France, typically finding ~3400 courses across all platforms combined.

## Scraping

Checking a discovered event's registration/participant list for club members. After discovery, each race URL is fetched and its participant list is parsed to find members of Run Event 86. This is done via dual matching (see below). Results are cached to avoid redundant requests.

## Club Matching

Regex-based detection of club members. The `club.patterns` list in `config.yml` contains regex patterns that match variants of the club name (e.g., "Run Event 86", "RunEvent86", "Run'Event 86"). The function `matches_club(club_name, patterns)` in `base.py` tests a participant's club field against these patterns.

## Name Matching

Matching participants by their full name rather than their club field. The `club.known_members` list in `config.yml` contains names of known club members (format: "LASTNAME Firstname"). The function `matches_known_member(name, known_members)` in `base.py` checks if a participant's name matches any known member. This catches members who left the club field empty or filled it incorrectly.

## Dual Matching

The combination of club matching and name matching. Every scraper uses both strategies to maximize detection: first checking the club field via regex, then checking participant names against the known members list. This is the default behavior for all platforms.

## Known Members

The list of 22 club member names stored in `config.yml` under `club.known_members`. Used for name-based matching. Format: `"LASTNAME Firstname"`. Must be kept up to date manually as members join or leave the club.

## Slug (Njuko)

The URL identifier for a Njuko event (e.g., `marathon-de-paris-2026`). Njuko events are accessed via `njuko.com/slug`. Slugs are stored in a persistent cache (`njuko_slugs.json`) because Njuko has no public event listing API -- slugs must be discovered from external sources like the Wayback Machine CDX.

## Scrape Cache

A persistent JSON file (`data/scrape_cache.json`) that stores the last scrape timestamp and results for each race URL. Prevents re-scraping events that were recently checked. Keyed by URL.

## TTL (Time-to-Live)

The cache expiration policy for the scrape cache. Two durations:
- **48 hours** for races where no members were found (no need to re-check frequently)
- **6 hours** for races where members were found (check more often for new registrations)

Defined as `CACHE_TTL_EMPTY` and `CACHE_TTL_WITH_MEMBERS` in `main.py`.

## Geocache

A persistent JSON file (`data/geocache.json`) that caches geocoding results. Keyed by lowercase location string. Values are `{lat, lng}` or `null` (for locations that failed geocoding). Avoids redundant API calls to BAN/Nominatim.

## BAN API

The Base Adresse Nationale API (`api-adresse.data.gouv.fr`), a free French government geocoding service. Used as the primary geocoder because it is fast, requires no API key, and has no rate limit. Returns GeoJSON with coordinates in `[lng, lat]` order. Only covers French addresses.

## Nominatim

OpenStreetMap's geocoding service, used as fallback when BAN fails (e.g., for international addresses). Rate limited to 1 request per second. Accessed via `nominatim.openstreetmap.org/search`.

## Manual Entry

A race added by hand in `config.yml` under the `races:` section, with `platform: manual`. Used for races that are not found by auto-discovery (e.g., small local events not listed on any supported platform). Manual entries include pre-filled member lists.

## Race Config

The dictionary describing a race to scrape. Fields: `platform`, `url`, `name`, `date`, `location`. Produced by `discover_races()` or defined manually in `config.yml`. Passed to a scraper's `scrape()` method.

## RaceResult

The output dataclass (defined in `base.py`) returned by each scraper's `scrape()` method. Contains: `id`, `name`, `date`, `location`, `platform`, `url`, `lat`, `lng`, `members[]` (list of `Member`), `member_count`, `last_scraped`. Serialized to JSON for the final output.

## Member

A dataclass (defined in `base.py`) representing a club member found in a registration list. Contains: `name` and `bib` (the race/category they registered for).
