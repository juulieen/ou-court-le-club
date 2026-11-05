"""Outils partagés du banc d'essai de géolocalisation (bench/geo).

Tout passe par un cache disque (data/bench/http_cache.json) : relancer le
banc ne refait aucune requête déjà faite, et deux méthodes qui posent la même
question au même service partagent la réponse.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
import unicodedata
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
BENCH_DIR = Path(os.environ.get("GEO_BENCH_DIR") or ROOT / "data" / "bench")
BENCH_DIR.mkdir(parents=True, exist_ok=True)
_CACHE_PATH = BENCH_DIR / "http_cache.json"
_cache: dict | None = None

BAN_URL = "https://api-adresse.data.gouv.fr/search/"

DEPT_NAMES = {
    "ain": "01", "aisne": "02", "allier": "03", "alpes de haute provence": "04",
    "hautes alpes": "05", "alpes maritimes": "06", "ardeche": "07", "ardennes": "08",
    "ariege": "09", "aube": "10", "aude": "11", "aveyron": "12",
    "bouches du rhone": "13", "calvados": "14", "cantal": "15", "charente": "16",
    "charente maritime": "17", "cher": "18", "correze": "19", "corse du sud": "2A",
    "haute corse": "2B", "cote d or": "21", "cotes d armor": "22", "creuse": "23",
    "dordogne": "24", "doubs": "25", "drome": "26", "eure": "27",
    "eure et loir": "28", "finistere": "29", "gard": "30", "haute garonne": "31",
    "gers": "32", "gironde": "33", "herault": "34", "ille et vilaine": "35",
    "indre": "36", "indre et loire": "37", "isere": "38", "jura": "39",
    "landes": "40", "loir et cher": "41", "loire": "42", "haute loire": "43",
    "loire atlantique": "44", "loiret": "45", "lot": "46", "lot et garonne": "47",
    "lozere": "48", "maine et loire": "49", "manche": "50", "marne": "51",
    "haute marne": "52", "mayenne": "53", "meurthe et moselle": "54", "meuse": "55",
    "morbihan": "56", "moselle": "57", "nievre": "58", "nord": "59", "oise": "60",
    "orne": "61", "pas de calais": "62", "puy de dome": "63",
    "pyrenees atlantiques": "64", "hautes pyrenees": "65", "pyrenees orientales": "66",
    "bas rhin": "67", "haut rhin": "68", "rhone": "69", "haute saone": "70",
    "saone et loire": "71", "sarthe": "72", "savoie": "73", "haute savoie": "74",
    "paris": "75", "seine maritime": "76", "seine et marne": "77", "yvelines": "78",
    "deux sevres": "79", "somme": "80", "tarn": "81", "tarn et garonne": "82",
    "var": "83", "vaucluse": "84", "vendee": "85", "vienne": "86",
    "haute vienne": "87", "vosges": "88", "yonne": "89", "territoire de belfort": "90",
    "essonne": "91", "hauts de seine": "92", "seine saint denis": "93",
    "val de marne": "94", "val d oise": "95", "guadeloupe": "971",
    "martinique": "972", "guyane": "973", "la reunion": "974", "reunion": "974",
    "mayotte": "976",
}


def norm(text: str) -> str:
    """Minuscules, sans accents, ponctuation → espace."""
    text = unicodedata.normalize("NFD", text or "")
    text = "".join(c for c in text if unicodedata.category(c) != "Mn").lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def _load() -> dict:
    global _cache
    if _cache is None:
        _cache = json.loads(_CACHE_PATH.read_text()) if _CACHE_PATH.exists() else {}
    return _cache


def save_cache() -> None:
    if _cache is not None:
        _CACHE_PATH.write_text(json.dumps(_cache, ensure_ascii=False))


def cached(key: str, fn):
    """Mémoïse fn() sur disque sous `key`."""
    cache = _load()
    h = hashlib.sha1(key.encode()).hexdigest()
    if h not in cache:
        cache[h] = fn()
        if len(cache) % 20 == 0:
            save_cache()
    return cache[h]


def http_json(url: str, params: dict | None = None, *, method="GET", json_body=None,
              headers=None, timeout=20, pause=0.0):
    key = json.dumps([method, url, params, json_body], sort_keys=True, ensure_ascii=False)

    def fetch():
        for attempt in range(3):
            try:
                if pause:
                    time.sleep(pause)
                r = requests.request(method, url, params=params, json=json_body,
                                     headers=headers, timeout=timeout)
                if r.status_code == 429:
                    time.sleep(5 * (attempt + 1))
                    continue
                if r.status_code >= 400:
                    return {"__error__": r.status_code, "body": r.text[:500]}
                return r.json()
            except Exception as e:  # réseau, JSON invalide
                err = str(e)
                time.sleep(1)
        return {"__error__": "exception", "body": err}

    return cached(key, fetch)


def ban(q: str, **params) -> list[dict]:
    """Résultats BAN : [{label, type, score, citycode, postcode, city, lat, lng}]."""
    data = http_json(BAN_URL, {"q": q[:200], "limit": params.pop("limit", 5), **params})
    out = []
    for f in (data or {}).get("features", []) if isinstance(data, dict) else []:
        p = f["properties"]
        lng, lat = f["geometry"]["coordinates"]
        out.append({
            "label": p.get("label"), "type": p.get("type"), "score": p.get("score", 0),
            "citycode": p.get("citycode", ""), "postcode": p.get("postcode", ""),
            "city": p.get("city", ""), "name": p.get("name", ""), "lat": lat, "lng": lng,
        })
    return out


def dept_of(citycode: str) -> str:
    if citycode.startswith("97"):
        return citycode[:3]
    return citycode[:2]


def parse_dept(location: str) -> str:
    """Département annoncé dans un lieu : « (86) », « , 86 », « , Vienne »,
    « 86 - Vienne ». Chaîne vide si rien."""
    m = re.search(r"\b(97[1-6]|2[AB]|\d{2})\b(?!\d)", location or "")
    if m and not re.search(r"\d{5}", location):
        return m.group(1)
    m = re.search(r"\b(\d{5})\b", location or "")
    if m:
        cp = m.group(1)
        return cp[:3] if cp.startswith("97") else cp[:2]
    n = norm(location)
    best = ""
    for name, code in DEPT_NAMES.items():
        if re.search(rf"(^| ){name}$", n) and len(name) > len(best):
            best, found = name, code
    return found if best else ""


def km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lng1, lat2, lng2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))
