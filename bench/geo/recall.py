"""Qualité brute d'un moteur de recherche, sans modèle : pour chaque course,
la commune de vérité apparaît-elle dans les 8 premiers résultats ?

Usage : SEARCH_ENGINE=searxng python -m bench.geo.recall
"""

from __future__ import annotations

import json
import random
import re

from bench.geo import methods as M
from bench.geo.common import BENCH_DIR, norm, save_cache


def main(sample: int = 80):
    data = json.loads((BENCH_DIR / "dataset.json").read_text())
    disco = [d for d in data if d["set"] == "disco"]
    items = [d for d in data if d["set"] == "club"] + random.Random(86).sample(disco, min(sample, len(disco)))
    found = empty = 0
    for it in items:
        name = re.sub(r"\b(19|20)\d{2}\b", "", it["name"]).strip(" -")
        res = M.web_search(f"{name} {(it.get('date') or '')[:4]} course lieu")
        if not res:
            empty += 1
            continue
        text = norm(" ".join(f"{r['title']} {r['snippet']} {r['url']}" for r in res))
        if norm(it["truth"]["city"]) in text:
            found += 1
    save_cache()
    out = {"moteur": M.ENGINE, "courses": len(items), "commune_trouvée": found, "aucun_résultat": empty}
    print(json.dumps(out, ensure_ascii=False))
    path = BENCH_DIR / "recall.json"
    allr = json.loads(path.read_text()) if path.exists() else {}
    allr[M.ENGINE] = out
    path.write_text(json.dumps(allr, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
