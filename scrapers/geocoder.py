"""Geocoding: a location string -> (lat, lng) of an existing commune.

Strict by design (bench/geo, Nov 2026: 0 error on 376 platform locations,
where the old « first BAN hit » placed 10 of them on a street or a hamlet
elsewhere in France):
1. OVERRIDES (manual fixes, always win);
2. the town part of the location must be a commune of the BAN (type
   municipality) — same name, in the department the location announces;
   with a department and no exact name, BAN's best commune of that
   department if it is confident (score >= 0.6);
3. no department: only a commune name that is unique in France;
4. still nothing and no French department: Nominatim, settlements only
   (foreign races: Eindhoven, Ireland...).
Otherwise no point — better than a point in the wrong region.

Results are cached in data/geocache.json (lowercase location -> {lat, lng}
or null).
"""

import json
import re
import time
import unicodedata
from pathlib import Path

import requests

CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "geocache.json"
# Bump when the resolution rules change: old answers are then ignored.
_CACHE_PREFIX = "strict1|"

BAN_URL = "https://api-adresse.data.gouv.fr/search"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# Manual corrections for locations that BAN/Nominatim geocode incorrectly.
# BAN often matches race names to street names (e.g. "marathon" -> "Rue de Marathon").
# Keys are lowercase. Values are (lat, lng) or None (= ungeocodable).
OVERRIDES: dict[str, tuple[float, float] | None] = {
    # Trail du Loup Blanc — Guéret (Creuse); no address in the Njuko edition.
    # The raw-name query hits BAN first: "Rue du Blanc Loup" in Vendeuil (02).
    "trail du loup blanc 2026 - 20e anniversaire": (46.17, 1.87),
    "loup blanc 2026 - 20e anniversaire": (46.17, 1.87),
    "loup blanc": (46.17, 1.87),
    # La Baïlla Run — Saint-Georges-lès-Baillargeaux (86). OnSinscrit exposes
    # the event nickname ("BaillaRun") in place of a town, which BAN can't match.
    "baillarun": (46.67, 0.41),
    "la baïlla run des galopins baillargeois": (46.67, 0.41),
    # Laval Urban Trail — Laval (Mayenne). BAN's bare "Laval" lands on a
    # hamlet in the Alpes-Maritimes.
    "laval": (48.06, -0.77),
    # Corrida de Magné — Magné (Deux-Sèvres, near Niort), not the Magné in
    # Haute-Vienne that BAN returns, nor the one in the Vienne.
    "magné": (46.31, -0.55),
    "magne": (46.31, -0.55),
    # Marathon de Nantes — BAN returns "Rue de Marathon" in Rennes
    "abalone marathon de nantes 2023": (47.24, -1.56),
    "abalone marathon de nantes 2024": (47.24, -1.56),
    "abalone marathon de nantes 2025": (47.24, -1.56),
    "nantes": (47.24, -1.56),
    # Marathon de la Mer — Boulogne-sur-Mer, not Bordeaux/Rennes
    "marathon de la mer - ed.2026": (50.73, 1.61),
    "marathon de la mer": (50.73, 1.61),
    # Route du Louvre — Lens (Pas-de-Calais), not a road in Sarthe
    "la route du louvre": (50.44, 2.82),
    "route du louvre": (50.44, 2.82),
    "la route du louvre - dimanche 10 mai 2026": (50.44, 2.82),
    # ENDORUN PARIS — Paris, not "Rue de Paris" in Brest
    "endorun paris 2025": (48.85, 2.41),
    "endorun paris": (48.85, 2.41),
    # Grands Trails d'Auvergne — Aubusson-d'Auvergne, not Saint-Nazaire
    "grands trails d'auvergne 2026": (45.75, 3.60),
    # Course des Crêtes — Espelette (Pays Basque), not Corsica
    "la course des cretes 2026": (43.34, -1.45),
    "course des cretes": (43.34, -1.45),
    # BEERUN — Joué-sur-Erdre (Loire-Atlantique), not Corsica
    "beerun 2025": (47.51, -1.43),
    "beerun": (47.51, -1.43),
    # 10 Miles des Baines — Capbreton (Landes), not the Alps
    "10 miles des baines 2026": (43.64, -1.42),
    # Luchon Aneto Trail — Pyrénées, not Corsica
    "luchon aneto trail 2025": (42.79, 0.59),
    "luchon aneto": (42.79, 0.59),
    # Choc des Guerriers — L'Isle-d'Espagnac (Charente), not Burgundy
    "choc des guerriers 2025": (45.66, 0.20),
    # Course des Pères Noël — Saint-Benoît 86 (near Poitiers)
    "st benoit": (46.55, 0.35),
    "la course des pères noel de st benoit le 20 décembre 2025": (46.55, 0.35),
    # Marathon Poitiers-Futuroscope — Futuroscope (Chasseneuil-du-Poitou), not Fleuré
    "poitiers-futuroscope": (46.66, 0.37),
    "futuroscope": (46.66, 0.37),
    "marathon poitiers-futuroscope 2026": (46.66, 0.37),
    "marathon poitiers-futuroscope 2025": (46.66, 0.37),
    # Half on the Head — Ireland, not in France
    "half on the head 2026": None,
    "on the head": None,
    # Saumur Marathon de la Loire — Saumur (Maine-et-Loire), not Rennes
    "saumur marathon de la loire 2026": (47.26, -0.08),
    "saumur marathon de la loire": (47.26, -0.08),
    # Marathon de Lille — Lille (Nord), not Nancy
    "marathon de lille - dimanche 25 octobre 2026": (50.63, 3.06),
    "lille - dimanche 25 octobre": (50.63, 3.06),
    "lille - dimanche": (50.63, 3.06),
    # Foulées d'Angoulême — Angoulême (Charente), not Paris suburbs
    "foulees d'angouleme": (45.65, 0.15),
    "foulées d'angoulême": (45.65, 0.15),
    # La Pastourelle — Salers (Cantal), not Brittany
    "la pastourelle 2026": (45.14, 2.50),
    "la pastourelle 2025": (45.14, 2.50),
    "la pastourelle": (45.14, 2.50),
    "pastourelle": (45.14, 2.50),
    # Épopée Royale — Saint-Cyr-sur-Loire / Tours, not Paris suburbs
    "epopee royale 2026": (47.40, 0.67),
    "epopee royale": (47.40, 0.67),
    # La Veni Vici — Pont du Gard / Nîmes (Gard), not Médoc coast
    "la veni vici 2026": (43.95, 4.54),
    "veni vici": (43.95, 4.54),
    # La Falaise (KTS Luberon) — Joucas (Vaucluse), not Brittany coast
    "la falaise 2026": (43.93, 5.25),
    "la falaise": (43.93, 5.25),
    # Trail du Caillou — Sebourg (Nord), not Bordeaux area
    "trail du caillou 2026": (50.35, 3.65),
    "trail du caillou": (50.35, 3.65),
    # Défi de la Colline — Saint-Georges-des-Gardes (Maine-et-Loire)
    "defi de la colline": (47.21, -0.72),
    "défi de la colline": (47.21, -0.72),
    # Nice Côte d'Azur by UTMB — Nice (Alpes-Maritimes)
    "nice côte d'azur by utmb®": (43.70, 7.27),
    "nice cote d'azur by utmb": (43.70, 7.27),
    # MaXi-Race — Annecy (Haute-Savoie)
    "maxi-race": (45.90, 6.13),
    "29/05/2026adidas terrex maxi-race 2026inscription": (45.90, 6.13),
    # Hendaia Trail — Hendaye (Pays Basque)
    "hendaia trail 2026": (43.36, -1.76),
    "hendaia trail": (43.36, -1.76),
    # Semi-Marathon d'Orvault — Orvault (Loire-Atlantique)
    "semi-marathon d'orvault": (47.27, -1.62),
    # Marathon 10-20km de Tours — Tours (Indre-et-Loire), not Marseille
    "marathon, 10 et 20 km de tours - 2025": (47.39, 0.69),
    "marathon, 10 et 20 km de tours - 2024": (47.39, 0.69),
    "marathon, 10 et 20 km de tours": (47.39, 0.69),
    "harmonie mutuelle marathon 10-20k tours 2026": (47.39, 0.69),
    # ASML Marathon Eindhoven — Eindhoven (Netherlands), not Grenoble
    "asml marathon eindhoven 2025": (51.44, 5.47),
    "asml marathon eindhoven 2024": (51.44, 5.47),
    "asml marathon eindhoven 2023": (51.44, 5.47),
    "asml marathon eindhoven": (51.44, 5.47),
    "asml-marathon-eindhoven-20231682333544914": (51.44, 5.47),
    # Abalone Marathon de Nantes 2026 — Nantes, not Rennes
    "abalone marathon de nantes 2026": (47.24, -1.56),
    # Foulées Saint Julien — Saint-Julien-l'Ars (Vienne). OnSinscrit writes
    # "Saint Julien Lars" without the apostrophe; BAN then picks the hamlet
    # "La Mare Saint-Julien" in Josselin (Morbihan).
    "saint julien lars, vienne": (46.56, 0.49),
    "saint julien lars": (46.56, 0.49),
    # La Ronde de Noël — La Meignanne (Longuenée-en-Anjou, 49). IPITOS writes
    # « La Meignane »; BAN only knows the merged commune.
    "la meignane": (47.52, -0.67),
    "la meignanne": (47.52, -0.67),
}


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
    """Lowercase, no accents, punctuation -> space."""
    text = unicodedata.normalize("NFD", text or "")
    text = "".join(c for c in text if unicodedata.category(c) != "Mn").lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def parse_dept(location: str) -> str:
    """Department announced in a location: « (86) », « , 86 », a postcode,
    « , Vienne ». Empty string if none."""
    location = location or ""
    m = re.search(r"\b(97[1-6]|2[AB]|\d{2})\b(?!\d)", location)
    if m and not re.search(r"\d{5}", location):
        return m.group(1)
    m = re.search(r"\b(\d{5})\b", location)
    if m:
        cp = m.group(1)
        return cp[:3] if cp.startswith("97") else cp[:2]
    # A department name only after a separator: « Chalonnes sur Loire » is in
    # Maine-et-Loire, not in the Loire.
    parts = re.split(r"[,(]| - ", location, maxsplit=1)
    n = norm(parts[1]) if len(parts) > 1 else ""
    best = ""
    for name in DEPT_NAMES:
        if re.search(rf"(^| ){name}$", n) and len(name) > len(best):
            best = name
    return DEPT_NAMES[best] if best else ""


def town_part(location: str) -> str:
    """« Saint Julien Lars, Vienne » -> « Saint Julien Lars » ; « Uzès (30) » -> « Uzès »."""
    town = re.split(r"[,(]| - ", location or "")[0]
    return re.sub(r"\b\d{5}\b", "", town).strip()


def _expand(town: str) -> str:
    town = re.sub(r"\bSte\b\.?", "Sainte", town, flags=re.I)
    return re.sub(r"\bSt\b\.?", "Saint", town, flags=re.I)


def _dept_of(citycode: str) -> str:
    return citycode[:3] if citycode.startswith("97") else citycode[:2]


class GeocoderUnavailable(Exception):
    """Network error or rate limit: the answer is unknown, do not cache it."""


def _ban_communes(town: str) -> list[dict]:
    for attempt in range(3):
        try:
            resp = requests.get(BAN_URL, timeout=10,
                                params={"q": town[:200], "type": "municipality", "limit": 10})
            if resp.status_code == 429:
                time.sleep(2 * (attempt + 1))
                continue
            resp.raise_for_status()
            features = resp.json().get("features", [])
            break
        except Exception:
            time.sleep(1)
    else:
        raise GeocoderUnavailable(town)
    out = []
    for f in features:
        p = f.get("properties", {})
        lng, lat = f["geometry"]["coordinates"]
        out.append({"city": p.get("city") or p.get("name") or "", "score": p.get("score", 0),
                    "dept": _dept_of(p.get("citycode", "")), "lat": lat, "lng": lng})
    return out


def resolve_commune(town: str, dept: str = "") -> dict | None:
    """Existing commune {city, dept, lat, lng} for a town name, or None.

    Same name in the given department; else BAN's best commune of that
    department when confident; without a department, only a name unique in
    France (there are 11 « Laval »)."""
    if not town:
        return None
    town = _expand(town)
    want = norm(town).replace(" ", "")
    res = _ban_communes(town)
    same = [r for r in res if norm(r["city"]).replace(" ", "") == want]
    if dept:
        for r in same:
            if r["dept"] == dept:
                return r
        in_dept = [r for r in res if r["dept"] == dept]
        if in_dept and in_dept[0]["score"] >= 0.6:
            return in_dept[0]
        return None
    if len({(r["city"], r["dept"]) for r in same}) == 1:
        return same[0]
    return None


def _load_cache() -> dict:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def override(text: str) -> tuple[float, float] | None | bool:
    """OVERRIDES entry for a location or race name; False if there is none."""
    return OVERRIDES.get((text or "").strip().lower(), False)


def geocode(location: str) -> tuple[float, float] | None:
    """(lat, lng) of the commune named by a location string, or None."""
    fixed = override(location)
    if fixed is not False:
        return fixed
    key = _CACHE_PREFIX + location.strip().lower()
    cache = _load_cache()
    if key in cache:
        entry = cache[key]
        return (entry["lat"], entry["lng"]) if entry else None

    dept = parse_dept(location)
    try:
        hit = resolve_commune(town_part(location), dept)
        coords = (hit["lat"], hit["lng"]) if hit else _geocode_nominatim(location, dept)
    except GeocoderUnavailable:
        return None  # try again next run

    cache[key] = {"lat": coords[0], "lng": coords[1]} if coords else None
    _save_cache(cache)
    return coords


def _geocode_nominatim(location: str, dept: str) -> tuple[float, float] | None:
    """Nominatim, towns, villages and hamlets only — places BAN has no
    commune for: Boyardville (a village of Saint-Georges-d'Oléron), former
    communes merged away (Saint-Georges-des-Gardes), races abroad. In France
    the place must carry the town's name and be in the announced department;
    without a department, that name must be unique (11 « Laval »). 1 req/s."""
    time.sleep(1)
    try:
        resp = requests.get(
            NOMINATIM_URL,
            params={"q": location, "format": "json", "limit": 5,
                    "featureType": "settlement", "addressdetails": 1},
            headers={"User-Agent": "runevent86-map"},
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json()
    except Exception as e:
        raise GeocoderUnavailable(location) from e
    if not results:
        return None
    first = results[0]
    if not dept and first.get("address", {}).get("country_code") != "fr":
        return float(first["lat"]), float(first["lon"])  # abroad

    want = norm(_expand(town_part(location))).replace(" ", "")
    same = []
    for r in results:
        address = r.get("address", {})
        if address.get("country_code") != "fr":
            continue
        if norm(r.get("name", "")).replace(" ", "") != want:
            continue
        code = address.get("ISO3166-2-lvl6") or ""
        if dept and code != f"FR-{dept}" and not (address.get("postcode") or "").startswith(dept):
            continue
        same.append(r)
    if len({(round(float(r["lat"]), 1), round(float(r["lon"]), 1)) for r in same}) != 1:
        return None
    return float(same[0]["lat"]), float(same[0]["lon"])
