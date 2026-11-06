"""Find the town of a race whose platform gives none (Njuko has no address).

One web search (Exa), then a free OpenRouter model reads the results and names
the starting commune; the answer counts only if resolve_commune() finds that
commune in the BAN. Measured on 146 races (bench/geo, Nov 2026): 134 right,
5 wrong (3 of them point-to-point races placed at the start), 7 no answer.

Each event is looked up once: the answer (or the failure, retried after
RETRY_AFTER_DAYS) is kept in data/lieux_auto.json, keyed by the race name
without its year, so next year's edition costs nothing. The paid search
result is stored before the model is asked: a model failure never pays for
the search twice. A run makes at most
MAX_SEARCHES searches (Exa bills ~0.007 $ each). Without EXA_API_KEY or
OPENROUTER_API_KEY nothing is searched and races keep no location.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from .geocoder import norm, resolve_commune

CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "lieux_auto.json"
EXA_URL = "https://api.exa.ai/search"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# Free models (0 $ key limit). The first one was measured; OpenRouter falls back
# to the next one when it is down or rate-limited.
MODELS = ["nvidia/nemotron-3-ultra-550b-a55b:free", "openrouter/free"]
MAX_SEARCHES = 20
RETRY_AFTER_DAYS = 30

_searches = 0
_warned = False


# (race id, name, town) of the towns found this run, for the ntfy check-me ping.
found_this_run: list[tuple[str, str, str]] = []


def event_key(name: str) -> str:
    return norm(re.sub(r"\b(19|20)\d{2}\b", " ", name or ""))


def _load() -> dict:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def _save(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _search(query: str) -> list[dict]:
    resp = requests.post(
        EXA_URL, timeout=30,
        headers={"x-api-key": os.environ["EXA_API_KEY"]},
        json={"query": query, "numResults": 8,
              "contents": {"highlights": {"numSentences": 3, "highlightsPerUrl": 1}}},
    )
    resp.raise_for_status()
    return [{"url": r.get("url", ""), "title": r.get("title") or "",
             "snippet": re.sub(r"\s+", " ", " ".join(r.get("highlights") or []))[:400]}
            for r in resp.json().get("results", [])]


def _ask(prompt: str) -> str:
    for attempt in range(3):
        try:
            resp = requests.post(
                OPENROUTER_URL, timeout=120,
                headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"},
                json={"models": MODELS, "temperature": 0,
                      "messages": [{"role": "user", "content": prompt}]},
            )
            data = resp.json()
        except Exception:
            time.sleep(10)
            continue
        if "choices" in data:
            return data["choices"][0]["message"].get("content") or ""
        if resp.status_code in (429, 502, 503) and "per-day" not in resp.text:
            time.sleep(15 * (attempt + 1))
            continue
        raise RuntimeError(f"OpenRouter {resp.status_code}: {resp.text[:200]}")
    raise RuntimeError("OpenRouter indisponible")


def _parse(text: str) -> tuple[str, str] | None:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    town = (d.get("commune") or "").strip()
    if not town or town.lower() in ("null", "inconnu", "unknown"):
        return None
    dept = str(d.get("departement") or "").strip().upper()
    if dept.isdigit() and len(dept) < 2:
        dept = dept.zfill(2)
    return town, dept


def _ask_town(name: str, date: str, results: list[dict]) -> str | None:
    listing = "\n".join(f"- {r['title']} — {r['snippet']} ({r['url']})" for r in results)
    prompt = (
        f"Course à pied en France : « {name} », le {date}.\n"
        f"Résultats d'une recherche web :\n{listing}\n\n"
        "D'après ces résultats SEULEMENT, dans quelle commune a lieu le départ ? "
        "Méfie-toi des homonymes (autre course, autre année).\n"
        'Réponds UNIQUEMENT par un objet JSON : {"commune": "...", "departement": "NN", '
        '"source": "url ou vide"}. Si tu n\'es pas sûr, réponds {"commune": null}.'
    )
    answer = _parse(_ask(prompt))
    hit = resolve_commune(*answer) if answer else None
    return f"{hit['city']} ({hit['dept']})" if hit else None


def find_location(race: dict) -> str | None:
    """« Commune (dd) » for a race without location, or None.

    The paid search result is saved before the model is asked: if the model
    fails (quota, outage), the next run asks again without searching again."""
    global _searches, _warned
    name, date = race.get("name", ""), race.get("date", "")
    key = event_key(name)
    if not key:
        return None
    cache = _load()
    entry = cache.get(key) or {}
    now = datetime.now(timezone.utc)
    if entry.get("location"):
        return entry["location"]
    if "location" in entry:  # searched, nothing found
        if now - datetime.fromisoformat(entry["tried"]) < timedelta(days=RETRY_AFTER_DAYS):
            return None
        entry = {}

    if not (os.environ.get("EXA_API_KEY") and os.environ.get("OPENROUTER_API_KEY")):
        if not _warned:
            print("  (EXA_API_KEY / OPENROUTER_API_KEY absentes : pas de recherche de lieu)")
            _warned = True
        return None
    try:
        if "results" not in entry:
            if _searches >= MAX_SEARCHES:
                return None
            _searches += 1
            year = (date or "")[:4]
            base = re.sub(r"\b(19|20)\d{2}\b", "", name).strip(" -")
            entry = {"results": _search(f"{base} {year} course lieu"),
                     "tried": now.isoformat(timespec="seconds")}
            cache[key] = entry
            _save(cache)
        found = _ask_town(name, date, entry["results"]) if entry["results"] else None
    except Exception as e:  # network, quota: next run picks up where this one stopped
        print(f"  Recherche de lieu impossible pour {name or '?'}: {e}")
        return None
    cache[key] = {"location": found, "tried": now.isoformat(timespec="seconds")}
    _save(cache)
    print(f"  Lieu cherché : {name} -> {found or 'introuvable'}")
    if found:
        found_this_run.append((race.get("id", ""), name, found))
    return found
