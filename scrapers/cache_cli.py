"""CLI tool to manage scraper caches (local + CI/prod).

Local cache:
    python -m scrapers.cache_cli stats
    python -m scrapers.cache_cli list [--platform PLATFORM] [--with-members]
    python -m scrapers.cache_cli clear --url PATTERN
    python -m scrapers.cache_cli clear --platform PLATFORM
    python -m scrapers.cache_cli clear --all
    python -m scrapers.cache_cli clear --empty
    python -m scrapers.cache_cli geocache list [PATTERN]
    python -m scrapers.cache_cli geocache clear PATTERN

CI/Prod cache (requires gh CLI):
    python -m scrapers.cache_cli ci list          # list CI caches
    python -m scrapers.cache_cli ci clear         # delete latest CI cache
    python -m scrapers.cache_cli ci clear --all   # delete all CI caches
    python -m scrapers.cache_cli ci run           # trigger a new CI run
    python -m scrapers.cache_cli ci run --fresh   # clear cache + trigger run

Sync between local and CI:
    python -m scrapers.cache_cli sync pull        # download CI cache to local
    python -m scrapers.cache_cli sync push        # upload local cache to CI (next run uses it)
    python -m scrapers.cache_cli sync diff        # show differences
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SCRAPE_CACHE = DATA_DIR / "scrape_cache.json"
GEOCACHE = DATA_DIR / "geocache.json"
NJUKO_SLUGS = DATA_DIR / "njuko_slugs.json"
RACES_JSON = DATA_DIR / "races.json"

# Files that are synced between local and CI
SYNC_FILES = [SCRAPE_CACHE, GEOCACHE, NJUKO_SLUGS, RACES_JSON]


def load_scrape_cache() -> dict:
    if SCRAPE_CACHE.exists():
        return json.loads(SCRAPE_CACHE.read_text(encoding="utf-8"))
    return {}


def save_scrape_cache(cache: dict):
    SCRAPE_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def load_geocache() -> dict:
    if GEOCACHE.exists():
        return json.loads(GEOCACHE.read_text(encoding="utf-8"))
    return {}


def save_geocache(cache: dict):
    GEOCACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def detect_platform(url: str) -> str:
    """Guess platform from URL."""
    patterns = {
        "klikego": "klikego.com",
        "njuko": "njuko.com",
        "onsinscrit": "onsinscrit.com",
        "protiming": "protiming.fr",
        "chronometrage": "chronometrage.com",
        "chronostart": "chrono-start",
        "3wsport": "3wsport.fr",
        "espace-competition": "espace-competition",
        "sportips": "sportips.fr",
        "timepulse": "timepulse.fr",
        "endurancechrono": "endurancechrono",
        "listino": "listino.fr",
        "ipitos": "ipitos.com",
        "utmb": "register-utmb.world",
        "sporkrono": "sporkrono-inscriptions.fr",
        "sports107": "sports107.com",
        "timeto": "timeto.com",
    }
    url_lower = url.lower()
    for name, domain in patterns.items():
        if domain in url_lower:
            return name
    return "unknown"


def cmd_list(args):
    cache = load_scrape_cache()
    entries = []
    for url, entry in cache.items():
        platform = detect_platform(url)
        members = entry.get("member_count", 0)
        if args.platform and platform != args.platform:
            continue
        if args.with_members and members == 0:
            continue
        entries.append((platform, members, url, entry.get("last_scraped", "?")))

    entries.sort(key=lambda x: (-x[1], x[0], x[2]))
    print(f"{len(entries)} entries:")
    for platform, members, url, last in entries:
        marker = f"  {members:3d} membres" if members > 0 else "     -     "
        print(f"  [{platform:15s}] {marker} | {url[:70]}")


def cmd_clear(args):
    cache = load_scrape_cache()
    to_remove = []

    if args.all:
        to_remove = list(cache.keys())
    elif args.empty:
        to_remove = [url for url, e in cache.items() if e.get("member_count", 0) == 0]
    elif args.platform:
        to_remove = [url for url in cache if detect_platform(url) == args.platform]
    elif args.url:
        pattern = args.url.lower()
        to_remove = [url for url in cache if pattern in url.lower()]
    else:
        print("Specify --all, --empty, --platform, or --url")
        return

    if not to_remove:
        print("Nothing to remove.")
        return

    print(f"Removing {len(to_remove)} entries:")
    for url in to_remove:
        members = cache[url].get("member_count", 0)
        print(f"  {'*' if members else ' '} {url[:80]}")
        del cache[url]

    save_scrape_cache(cache)
    print(f"Done. {len(cache)} entries remaining.")


def cmd_stats(args):
    cache = load_scrape_cache()
    geocache = load_geocache()

    platforms = {}
    total_members = 0
    with_members = 0
    for url, entry in cache.items():
        platform = detect_platform(url)
        members = entry.get("member_count", 0)
        platforms.setdefault(platform, {"total": 0, "with_members": 0})
        platforms[platform]["total"] += 1
        if members > 0:
            platforms[platform]["with_members"] += 1
            with_members += 1
            total_members += members

    print(f"=== Scrape cache: {len(cache)} entries ===")
    print(f"  With members: {with_members}")
    print(f"  Empty: {len(cache) - with_members}")
    print(f"\nPer platform:")
    for p, stats in sorted(platforms.items(), key=lambda x: -x[1]["total"]):
        print(f"  {p:20s} {stats['total']:5d} total, {stats['with_members']:3d} with members")

    geo_ok = sum(1 for v in geocache.values() if v is not None)
    geo_fail = sum(1 for v in geocache.values() if v is None)
    print(f"\n=== Geocache: {len(geocache)} entries ===")
    print(f"  Resolved: {geo_ok}")
    print(f"  Failed: {geo_fail}")


def cmd_geocache(args):
    cache = load_geocache()

    if args.action == "list":
        pattern = (args.pattern or "").lower()
        entries = [(k, v) for k, v in cache.items() if pattern in k]
        entries.sort()
        print(f"{len(entries)} entries:")
        for key, val in entries:
            if val:
                print(f"  {key:50s} -> ({val['lat']:.2f}, {val['lng']:.2f})")
            else:
                print(f"  {key:50s} -> FAILED")

    elif args.action == "clear":
        if not args.pattern:
            print("Specify a pattern to clear.")
            return
        pattern = args.pattern.lower()
        to_remove = [k for k in cache if pattern in k]
        for k in to_remove:
            print(f"  Removed: {k}")
            del cache[k]
        save_geocache(cache)
        print(f"Removed {len(to_remove)} entries.")


def cmd_show(args):
    """Show detailed info about a cache entry."""
    cache = load_scrape_cache()
    pattern = args.pattern.lower()
    matches = [(url, entry) for url, entry in cache.items() if pattern in url.lower()]

    if not matches:
        print(f"No entries matching '{args.pattern}'.")
        return

    for url, entry in matches:
        platform = detect_platform(url)
        members = entry.get("member_count", 0)
        last = entry.get("last_scraped", "?")
        data = entry.get("data")

        print(f"\n{'─'*60}")
        print(f"  URL:        {url}")
        print(f"  Platform:   {platform}")
        print(f"  Members:    {members}")
        print(f"  Scraped:    {last}")

        if data and isinstance(data, dict):
            print(f"  Name:       {data.get('name', '?')}")
            print(f"  Date:       {data.get('date', '?')}")
            print(f"  Location:   {data.get('location', '?')}")
            print(f"  Race type:  {data.get('race_type', '?')}")
            print(f"  Distances:  {data.get('distances', [])}")
            for m in data.get("members", []):
                print(f"    👤 {m.get('name', '?'):30s} — {m.get('bib', '')}")
        elif members == 0:
            print(f"  (no data cached — 0 members)")
        print(f"{'─'*60}")


def cmd_rescrape(args):
    """Clear specific entries and immediately re-scrape them."""
    import yaml

    cache = load_scrape_cache()
    pattern = args.pattern.lower()
    matches = [url for url in cache if pattern in url.lower()]

    if not matches:
        print(f"No cache entries matching '{args.pattern}'.")
        return

    # Show what we'll re-scrape
    print(f"Will re-scrape {len(matches)} URL(s):")
    for url in matches:
        members = cache[url].get("member_count", 0)
        print(f"  {'*' if members else ' '} {url[:80]}")

    # Clear them from cache
    for url in matches:
        del cache[url]
    save_scrape_cache(cache)

    # Load config
    config_path = ROOT / "config.yml"
    if not config_path.exists():
        print("config.yml not found — cleared cache but cannot re-scrape.")
        return

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    club = config.get("club", {})
    patterns = club.get("patterns", [])
    known_members = club.get("known_members", [])

    # Import scrape function
    from .main import scrape_race

    # Re-scrape each URL
    print(f"\nRe-scraping...")
    found = 0
    for url in matches:
        # Build a minimal race_config
        platform = detect_platform(url)
        rc = {"url": url, "platform": platform, "name": "?", "date": "", "location": ""}
        try:
            data = scrape_race(rc, patterns, known_members)
        except Exception as e:
            print(f"  ✗ {url[:60]} — {e}")
            data = None

        member_count = data.get("member_count", 0) if data else 0
        cache_data = None
        if data and member_count > 0:
            cache_data = {k: v for k, v in data.items() if k not in ("lat", "lng")}
            found += 1
            print(f"  ✓ {data.get('name', url)[:50]} — {member_count} membre(s)")
            for m in data.get("members", []):
                print(f"    👤 {m.get('name', '?')} — {m.get('bib', '')}")
        else:
            print(f"  · {url[:60]} — 0 membres")

        from datetime import datetime, timezone
        cache[url] = {
            "last_scraped": datetime.now(timezone.utc).isoformat(),
            "member_count": member_count,
            "data": cache_data,
        }

    save_scrape_cache(cache)
    print(f"\nDone. {found}/{len(matches)} avec membres.")


def _gh(*args) -> subprocess.CompletedProcess:
    """Run a gh CLI command."""
    return subprocess.run(["gh", *args], capture_output=True, text=True)


def cmd_ci(args):
    action = args.action

    if action == "list":
        result = _gh("cache", "list", "--json", "key,createdAt,sizeInBytes")
        if result.returncode != 0:
            print(f"Error: {result.stderr.strip()}")
            return
        try:
            all_caches = json.loads(result.stdout)
        except Exception:
            print("No CI caches found.")
            return
        caches = [c for c in all_caches if c.get("key", "").startswith("scraper-data-")]
        if not caches:
            print("No CI scraper caches found.")
            return
        print(f"CI caches ({len(caches)}):")
        for entry in caches:
            size_kb = entry.get("sizeInBytes", 0) / 1024
            print(f"  {entry['key']:45s} {size_kb:8.1f} KB  {entry.get('createdAt', '?')}")

    elif action == "clear":
        # Get all scraper-data cache keys
        result = _gh("cache", "list", "--json", "key")
        if result.returncode != 0:
            print(f"Error: {result.stderr.strip()}")
            return
        try:
            all_caches = json.loads(result.stdout)
        except Exception:
            all_caches = []
        keys = [c["key"] for c in all_caches if c.get("key", "").startswith("scraper-data-")]
        if not keys:
            print("No CI caches to clear.")
            return

        if args.all:
            to_delete = keys
        else:
            # Delete only the latest (most recent restore key)
            to_delete = [keys[0]]

        for key in to_delete:
            r = _gh("cache", "delete", key)
            if r.returncode == 0:
                print(f"  Deleted: {key}")
            else:
                print(f"  Failed: {key} ({r.stderr.strip()})")
        print(f"Deleted {len(to_delete)} CI cache(s).")

    elif action == "run":
        if args.fresh:
            print("Clearing CI caches first...")
            args_clear = argparse.Namespace(action="clear", all=True)
            cmd_ci(args_clear)
            print()

        print("Triggering CI workflow...")
        result = _gh("workflow", "run", "scrape.yml")
        if result.returncode == 0:
            print(f"Workflow triggered! {result.stdout.strip()}")
            # Get the run URL
            result2 = _gh("run", "list", "--limit", "1", "--json", "url", "-q", ".[0].url")
            if result2.stdout.strip():
                print(f"  {result2.stdout.strip()}")
        else:
            print(f"Error: {result.stderr.strip()}")


def cmd_sync(args):
    action = args.action

    if action == "pull":
        # Download CI cache artifact to local
        print("Downloading CI cache from latest workflow run...")

        # Find latest successful scrape run
        result = _gh("run", "list", "--workflow", "scrape.yml", "--status", "success",
                      "--limit", "1", "--json", "databaseId,createdAt")
        if result.returncode != 0:
            print(f"Error: {result.stderr.strip()}")
            return
        try:
            runs = json.loads(result.stdout)
        except Exception:
            print("No successful runs found.")
            return
        if not runs:
            print("No successful scrape runs found.")
            return

        run_id = runs[0]["databaseId"]
        created = runs[0].get("createdAt", "?")
        print(f"  Latest run: {run_id} ({created})")

        # Download artifact
        with tempfile.TemporaryDirectory() as tmp:
            result = _gh("run", "download", str(run_id), "--name", "scraper-data", "--dir", tmp)
            if result.returncode != 0:
                print(f"  Error downloading artifact: {result.stderr.strip()}")
                print("  (The CI workflow may not have the upload-artifact step yet.)")
                print("  Run the workflow once after the latest push to generate the artifact.")
                return

            # Copy files to local data/
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            count = 0
            for src in Path(tmp).rglob("*.json"):
                # Artifact preserves the data/ prefix
                dest = DATA_DIR / src.name
                local_size = dest.stat().st_size if dest.exists() else 0
                remote_size = src.stat().st_size
                shutil.copy2(src, dest)
                count += 1
                marker = " (new)" if local_size == 0 else f" ({local_size} -> {remote_size})"
                print(f"  {dest.name}{marker}")

        print(f"Synced {count} files from CI to local.")

    elif action == "push":
        # "Push" local cache to CI = clear CI cache so next run rebuilds from scratch,
        # but since CI can't directly import local files, we trigger a fresh run.
        # The local cache stays as-is for local dev.
        print("CI cannot directly import local cache files.")
        print("Instead, the recommended workflow is:")
        print("  1. Make fixes locally (geocache corrections, slug additions, etc.)")
        print("  2. Commit code changes (geocoder.py OVERRIDES, njuko.py _SEED_SLUGS)")
        print("  3. Clear CI cache:  python -m scrapers.cache_cli ci clear --all")
        print("  4. Trigger fresh run: python -m scrapers.cache_cli ci run")
        print()
        if input("Do this now? [y/N] ").strip().lower() == "y":
            args_run = argparse.Namespace(action="run", fresh=True)
            cmd_ci(args_run)

    elif action == "diff":
        # Compare local vs CI cache (download CI to temp and compare)
        print("Downloading CI cache for comparison...")

        result = _gh("run", "list", "--workflow", "scrape.yml", "--status", "success",
                      "--limit", "1", "--json", "databaseId")
        if result.returncode != 0:
            print(f"Error: {result.stderr.strip()}")
            return
        try:
            runs = json.loads(result.stdout)
        except Exception:
            runs = []
        if not runs:
            print("No successful runs found.")
            return

        run_id = runs[0]["databaseId"]

        with tempfile.TemporaryDirectory() as tmp:
            result = _gh("run", "download", str(run_id), "--name", "scraper-data", "--dir", tmp)
            if result.returncode != 0:
                print(f"  Error: {result.stderr.strip()}")
                return

            for filename in ["scrape_cache.json", "geocache.json", "njuko_slugs.json", "races.json"]:
                local_path = DATA_DIR / filename
                remote_path = None
                for p in Path(tmp).rglob(filename):
                    remote_path = p
                    break

                local_exists = local_path.exists()
                remote_exists = remote_path is not None

                if not local_exists and not remote_exists:
                    continue

                local_size = local_path.stat().st_size if local_exists else 0
                remote_size = remote_path.stat().st_size if remote_exists else 0

                if not local_exists:
                    print(f"  {filename:25s}  LOCAL: missing  |  CI: {remote_size:,} bytes")
                elif not remote_exists:
                    print(f"  {filename:25s}  LOCAL: {local_size:,} bytes  |  CI: missing")
                elif local_size == remote_size:
                    print(f"  {filename:25s}  identical ({local_size:,} bytes)")
                else:
                    # Count entries for scrape_cache and geocache
                    detail = ""
                    if filename in ("scrape_cache.json", "geocache.json"):
                        try:
                            local_data = json.loads(local_path.read_text())
                            remote_data = json.loads(remote_path.read_text())
                            local_n = len(local_data)
                            remote_n = len(remote_data)
                            detail = f"  ({local_n} vs {remote_n} entries)"
                        except Exception:
                            pass
                    print(f"  {filename:25s}  LOCAL: {local_size:,} bytes  |  CI: {remote_size:,} bytes{detail}")


def main():
    parser = argparse.ArgumentParser(description="Manage scraper caches (local + CI)")
    sub = parser.add_subparsers(dest="command")

    # list
    p_list = sub.add_parser("list", help="List local cache entries")
    p_list.add_argument("--platform", "-p", help="Filter by platform")
    p_list.add_argument("--with-members", "-m", action="store_true", help="Only show entries with members")

    # clear
    p_clear = sub.add_parser("clear", help="Clear local cache entries")
    p_clear.add_argument("--url", "-u", help="Clear entries matching URL pattern")
    p_clear.add_argument("--platform", "-p", help="Clear all entries for a platform")
    p_clear.add_argument("--all", action="store_true", help="Clear entire cache")
    p_clear.add_argument("--empty", action="store_true", help="Clear entries with 0 members")

    # show
    p_show = sub.add_parser("show", help="Show detailed info about a cache entry")
    p_show.add_argument("pattern", help="URL pattern to search for")

    # rescrape
    p_rescrape = sub.add_parser("rescrape", help="Clear + immediately re-scrape matching URLs")
    p_rescrape.add_argument("pattern", help="URL pattern to match")

    # stats
    sub.add_parser("stats", help="Show cache statistics")

    # geocache
    p_geo = sub.add_parser("geocache", help="Manage geocoding cache")
    p_geo.add_argument("action", choices=["list", "clear"])
    p_geo.add_argument("pattern", nargs="?", default="")

    # ci
    p_ci = sub.add_parser("ci", help="Manage CI/prod cache and workflows")
    p_ci.add_argument("action", choices=["list", "clear", "run"])
    p_ci.add_argument("--all", action="store_true", help="Clear all CI caches (not just latest)")
    p_ci.add_argument("--fresh", action="store_true", help="Clear cache before running")

    # sync
    p_sync = sub.add_parser("sync", help="Sync cache between local and CI")
    p_sync.add_argument("action", choices=["pull", "push", "diff"])

    args = parser.parse_args()

    if args.command == "list":
        cmd_list(args)
    elif args.command == "show":
        cmd_show(args)
    elif args.command == "rescrape":
        cmd_rescrape(args)
    elif args.command == "clear":
        cmd_clear(args)
    elif args.command == "stats":
        cmd_stats(args)
    elif args.command == "geocache":
        cmd_geocache(args)
    elif args.command == "ci":
        cmd_ci(args)
    elif args.command == "sync":
        cmd_sync(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
