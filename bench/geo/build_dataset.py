"""Construit le jeu de vérité terrain du banc d'essai.

Sources :
- « club » : courses du club (site en ligne + archive locale). Vérité = la
  commune du lieu donné par la plateforme, ou le tableau _EVENT_LOCATIONS de
  njuko.py, ou un étiquetage manuel (data/bench/labels.json).
- « disco » : courses découvertes sur SportsnConnect, Adeorun et Protiming
  (data/bench/discovery.json), dont la plateforme donne ville + département.

La vérité est toujours une commune existante (BAN, type municipality) dans le
département annoncé. Une course dont on ne peut pas établir la commune avec
certitude est écartée (ou listée à étiqueter pour le jeu club).

Usage : python -m bench.geo.build_dataset
"""

from __future__ import annotations

import json
import re

import requests

from bench.geo.common import BENCH_DIR, ROOT, ban, dept_of, norm, parse_dept, save_cache
from scrapers.njuko import known_location

LIVE_URL = "https://juulieen.github.io/ou-court-le-club/data/races.json"


def town_part(location: str) -> str:
    """« Saint Julien Lars, Vienne » → « Saint Julien Lars » ; « Uzès (30) » → « Uzès »."""
    t = re.split(r"[,(]| - ", location or "")[0]
    return t.strip()


def expand(town: str) -> str:
    t = re.sub(r"\bSte\b\.?", "Sainte", town, flags=re.I)
    return re.sub(r"\bSt\b\.?", "Saint", t, flags=re.I)


def commune(town: str, dept: str) -> dict | None:
    """Commune dont le nom correspond exactement, dans le département donné."""
    if not town:
        return None
    want = norm(expand(town)).replace(" ", "")
    for r in ban(expand(town), type="municipality", limit=10):
        if dept and dept_of(r["citycode"]) != dept:
            continue
        if norm(r["city"] or r["name"]).replace(" ", "") == want:
            return {"lat": r["lat"], "lng": r["lng"], "city": r["city"],
                    "dept": dept_of(r["citycode"])}
    return None


def truth_from_location(location: str) -> dict | None:
    dept = parse_dept(location)
    if not dept:
        return None
    return commune(town_part(location), dept)


def club_races() -> list[dict]:
    live = requests.get(LIVE_URL, timeout=30).json()["races"]
    arch_path = ROOT / "data" / "races_archive.json"
    arch = json.loads(arch_path.read_text())["races"] if arch_path.exists() else []
    seen, out = set(), []
    for r in live + arch:
        key = r.get("url") or r["name"]
        if key in seen:
            continue
        seen.add(key)
        # Jamais de membres dans le jeu : seulement la course.
        out.append({k: r.get(k) for k in ("name", "date", "location", "platform", "url", "lat", "lng")})
    return out


def main():
    labels_path = BENCH_DIR / "labels.json"
    labels = json.loads(labels_path.read_text()) if labels_path.exists() else {}
    items, to_label = [], []

    for r in club_races():
        loc = r.get("location") or ""
        truth, src = None, ""
        lkey = r.get("url") or r["name"]
        if lkey in labels:
            lab = labels[lkey]
            truth = commune(lab["town"], lab["dept"]) if lab.get("town") else None
            src = "label" if truth else ""
            if lab.get("town") and not truth:
                print("étiquette introuvable :", lab)
        if not truth and loc:
            truth, src = truth_from_location(loc), "platform"
        if not truth and r["platform"] == "njuko":
            kl = known_location(r["name"])
            if kl:
                truth, src = truth_from_location(kl), "event_locations"
        item = {**r, "set": "club", "truth": truth, "truth_src": src}
        if truth:
            items.append(item)
        else:
            to_label.append(r)

    disco = json.loads((BENCH_DIR / "discovery.json").read_text())
    for platform, races in disco.items():
        for r in races:
            if "test" in norm(r["name"]).split():
                continue
            truth = truth_from_location(r.get("location") or "")
            if truth:
                items.append({**{k: r.get(k) for k in ("name", "date", "location", "platform", "url")},
                              "set": "disco", "truth": truth, "truth_src": "platform"})

    save_cache()
    (BENCH_DIR / "dataset.json").write_text(json.dumps(items, ensure_ascii=False, indent=1))
    (BENCH_DIR / "to_label.json").write_text(json.dumps(to_label, ensure_ascii=False, indent=1))
    n_club = sum(1 for i in items if i["set"] == "club")
    print(f"{len(items)} courses avec vérité ({n_club} club, {len(items) - n_club} découverte)")
    print(f"{len(to_label)} courses du club à étiqueter : data/bench/to_label.json")


if __name__ == "__main__":
    main()
