"""Lance les méthodes sur le jeu de vérité et compte justes / faux / sans réponse.

Juste = à moins de TOL_KM du centre de la commune de vérité.

Usage :
  python -m bench.geo.run                     # méthodes sans modèle
  python -m bench.geo.run --llm MODELE [...]  # + B2/B3 avec ces modèles
  python -m bench.geo.run --llm-sample 80     # nb de courses « disco » pour les modèles
"""

from __future__ import annotations

import argparse
import json
import random
import sys

from bench.geo import methods as M
from bench.geo.common import BENCH_DIR, km, save_cache

TOL_KM = 15


def evaluate(items, name, fn):
    rows = []
    for i, it in enumerate(items):
        try:
            res = fn(it)
        except Exception as e:  # une méthode qui plante = sans réponse
            res = None
            print(f"  ! {name} {it['name']}: {e}", file=sys.stderr)
        if res is None:
            verdict, dist = "sans", None
        else:
            dist = km((res["lat"], res["lng"]), (it["truth"]["lat"], it["truth"]["lng"]))
            verdict = "juste" if dist <= TOL_KM else "faux"
        rows.append({"name": it["name"], "set": it["set"], "platform": it["platform"],
                     "truth": f"{it['truth']['city']} ({it['truth']['dept']})",
                     "verdict": verdict, "km": round(dist) if dist is not None else None,
                     "why": (res or {}).get("why", "")})
        if i % 25 == 24:
            save_cache()
    save_cache()
    return rows


def summary(rows):
    out = {}
    for s in ("club", "disco"):
        r = [x for x in rows if x["set"] == s]
        if not r:
            continue
        j = sum(x["verdict"] == "juste" for x in r)
        f = sum(x["verdict"] == "faux" for x in r)
        out[s] = {"n": len(r), "juste": j, "faux": f, "sans": len(r) - j - f}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", nargs="*", default=[])
    ap.add_argument("--llm-sample", type=int, default=80)
    ap.add_argument("--only", nargs="*", default=None, help="noms de méthodes à lancer")
    args = ap.parse_args()

    data = json.loads((BENCH_DIR / "dataset.json").read_text())
    with_loc = [d for d in data if d.get("location")]
    rnd = random.Random(86)
    disco = [d for d in data if d["set"] == "disco"]
    llm_items = [d for d in data if d["set"] == "club"] + rnd.sample(disco, min(args.llm_sample, len(disco)))

    plan = [
        ("A0_actuel", "A", with_loc, M.A0_actuel),
        ("A1_strict", "A", with_loc, M.A1_strict),
        ("B0_actuel", "B", data, M.B0_actuel),
        ("B1_commune_du_nom", "B", data, M.B1_commune_du_nom),
        ("B1b_commune_du_nom_strict", "B", data, M.B1b_commune_du_nom_strict),
    ]
    eng = "" if M.ENGINE == "ddg" else f"[{M.ENGINE}]"
    for model in args.llm:
        plan.append((f"B2_llm_seul:{model}", "B", llm_items, M.B2_llm_seul(model)))
        plan.append((f"B3_recherche_llm{eng}:{model}", "B", llm_items, M.B3_recherche_llm(model)))
        plan.append((f"B4_agent{eng}:{model}", "B", llm_items, M.B4_agent(model)))
    if args.only:
        plan = [p for p in plan if any(p[0].startswith(o) for o in args.only)]

    res_path = BENCH_DIR / "results.json"
    results = json.loads(res_path.read_text()) if res_path.exists() else {}
    for name, task, items, fn in plan:
        print(f"== {name} ({len(items)} courses)", flush=True)
        rows = evaluate(items, name, fn)
        results[name] = {"task": task, "summary": summary(rows), "rows": rows}
        print("  ", results[name]["summary"], flush=True)
        res_path.write_text(json.dumps(results, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
