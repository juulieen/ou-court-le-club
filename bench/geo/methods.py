"""Méthodes de géolocalisation comparées par le banc d'essai.

Chaque méthode prend une course {name, date, location} et renvoie
{"lat", "lng", "why"} ou None (pas de réponse). Aucune ne lit OVERRIDES ni
le geocache de production : on mesure le mécanisme, pas les corrections
manuelles accumulées.

Épreuve A (lieu donné par la plateforme) : A0_actuel, A1_strict.
Épreuve B (nom + date seulement, cas Njuko) : B0_actuel, B1_commune_du_nom,
B2_llm_seul:<modèle>, B3_recherche_llm:<modèle>.
"""

from __future__ import annotations

import html
import json
import os
import re
import time
from pathlib import Path

from bench.geo.common import BENCH_DIR, ban, cached, dept_of, http_json, norm, parse_dept
from bench.geo.build_dataset import commune, expand, town_part

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


# --- Géocodeur actuel (copie fidèle de scrapers/geocoder.py, sans cache ni OVERRIDES) ---

def current_geocode(q: str):
    res = ban(q, limit=1)
    if res:
        return {"lat": res[0]["lat"], "lng": res[0]["lng"], "why": f"BAN {res[0]['type']} {res[0]['label']}"}
    data = http_json(NOMINATIM_URL, {"q": q, "format": "json", "limit": 1},
                     headers={"User-Agent": "runevent86-geo-bench"}, pause=1.1)
    if isinstance(data, list) and data:
        return {"lat": float(data[0]["lat"]), "lng": float(data[0]["lon"]),
                "why": f"Nominatim {data[0].get('display_name', '')[:60]}"}
    return None


def A0_actuel(race):
    return current_geocode(race["location"]) if race.get("location") else None


# --- Géocodeur strict ---

def resolve_commune(town: str, dept: str = ""):
    """Commune existante : nom exact (dans le département s'il est donné),
    sinon la meilleure commune du département si BAN est assez sûr."""
    if not town:
        return None
    exact = commune(town, dept)
    if exact:
        return {**exact, "why": f"commune exacte {exact['city']} ({exact['dept']})"}
    res = ban(expand(town), type="municipality", limit=10)
    if dept:
        res = [r for r in res if dept_of(r["citycode"]) == dept]
        if res and res[0]["score"] >= 0.6:
            r = res[0]
            return {"lat": r["lat"], "lng": r["lng"], "city": r["city"], "dept": dept,
                    "why": f"commune proche {r['city']} ({dept}) score {r['score']:.2f}"}
        return None
    # Sans département : seulement un nom exact et unique en France.
    want = norm(expand(town)).replace(" ", "")
    same = [r for r in res if norm(r["city"]).replace(" ", "") == want]
    if len({r["citycode"] for r in same}) == 1:
        r = same[0]
        return {"lat": r["lat"], "lng": r["lng"], "city": r["city"], "dept": dept_of(r["citycode"]),
                "why": f"commune unique {r['city']} ({dept_of(r['citycode'])})"}
    return None


def A1_strict(race):
    loc = race.get("location") or ""
    if not loc:
        return None
    return resolve_commune(town_part(loc), parse_dept(loc))


# --- Épreuve B : nom seulement ---

def B0_actuel(race):
    """Ce que fait la production pour une course Njuko sans lieu : le nom,
    puis les morceaux extraits par _extract_location_from_name."""
    from scrapers.main import _extract_location_from_name
    name = race["name"]
    queries = [name] + [c for c in _extract_location_from_name(name) if c.lower() != name.lower()]
    for q in queries:
        r = current_geocode(q)
        if r:
            return r
    return None


_STOP = set(norm("""la le les de des du d l a au aux et en sur sous trail course courses
    marathon semi run running ultra foulee foulees corrida cross nature nocturne
    nuit night urban ekiden km rando randonnee marche edition tour boucles boucle
    challenge festival trails grand petit raid relais duo solo""").split())


def B1_commune_du_nom(race):
    """Cherche une commune dont le nom figure tel quel dans le nom de la
    course (groupes de 1 à 5 mots, le plus long d'abord). Abstention si le
    nom désigne plusieurs communes homonymes."""
    words = re.sub(r"\b(19|20)\d{2}\b|\d+", " ", race["name"])
    words = re.sub(r"[’'`]", "' ", words)
    tokens = [t for t in re.split(r"[\s,/()«»\"!?:;.\-–]+", words) if t]
    seen = set()
    for size in range(5, 0, -1):
        for i in range(len(tokens) - size + 1):
            chunk = " ".join(tokens[i:i + size]).strip("-' ")
            n = norm(chunk)
            if not n or n in seen or all(w in _STOP for w in n.split()) or len(n) < 3:
                continue
            seen.add(n)
            want = norm(expand(chunk)).replace(" ", "")
            res = ban(expand(chunk), type="municipality", limit=10)
            same = [r for r in res if norm(r["city"]).replace(" ", "") == want]
            codes = {r["citycode"] for r in same}
            if len(codes) == 1:
                r = same[0]
                return {"lat": r["lat"], "lng": r["lng"],
                        "why": f"« {chunk} » = {r['city']} ({dept_of(r['citycode'])})"}
    return None


# --- Modèles OpenRouter ---

_KEY_PATH = BENCH_DIR / ".orkey.json"
OR_URL = "https://openrouter.ai/api/v1/chat/completions"
_last_call = [0.0]


def _key() -> str:
    return os.environ.get("OPENROUTER_API_KEY") or json.loads(_KEY_PATH.read_text())["key"]


def llm(model: str, prompt: str) -> str:
    """Réponse texte du modèle (mise en cache). Chaîne vide en cas d'échec."""
    def call():
        for attempt in range(6):
            wait = 3.2 - (time.time() - _last_call[0])  # ≤ 20 requêtes/min
            if wait > 0:
                time.sleep(wait)
            _last_call[0] = time.time()
            import requests
            try:
                r = requests.post(OR_URL, timeout=120, headers={"Authorization": f"Bearer {_key()}"},
                                  json={"model": model, "temperature": 0,
                                        "messages": [{"role": "user", "content": prompt}]})
                d = r.json()
            except Exception:
                time.sleep(5)
                continue
            if "choices" in d:
                return {"text": d["choices"][0]["message"].get("content") or ""}
            code = (d.get("error") or {}).get("code")
            if code in (429, 502, 503, 408) or r.status_code >= 500:
                time.sleep(10 * (attempt + 1))
                continue
            return {"text": "", "error": d.get("error")}
        return {"text": "", "error": "retries"}
    res = cached(json.dumps(["llm", model, prompt], ensure_ascii=False), call)
    return res.get("text", "")


def _parse_answer(text: str):
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
    dept = dept.zfill(2) if dept.isdigit() and len(dept) < 2 else dept
    return town, dept, d.get("source") or ""


_FORMAT = ('Réponds UNIQUEMENT par un objet JSON : {"commune": "...", "departement": "NN", '
           '"source": "url ou vide"}. Si tu n\'es pas sûr, réponds {"commune": null}.')


def B2_llm_seul(model: str):
    def method(race):
        prompt = (f"Course à pied en France : « {race['name']} », le {race['date']}.\n"
                  f"Dans quelle commune a-t-elle lieu (départ) ?\n{_FORMAT}")
        ans = _parse_answer(llm(model, prompt))
        if not ans:
            return None
        town, dept, _ = ans
        r = resolve_commune(town, dept)
        return {**r, "why": f"modèle : {town} ({dept}) → {r['why']}"} if r else None
    return method


# --- Recherche web (DuckDuckGo, version « lite », sans clé) ---

ENGINE = os.environ.get("SEARCH_ENGINE", "ddg")  # ddg | searxng
SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://localhost:8080")


def web_search(q: str, n: int = 8) -> list[dict]:
    return searxng_search(q, n) if ENGINE == "searxng" else ddg_search(q, n)


def searxng_search(q: str, n: int = 8) -> list[dict]:
    """SearXNG (métamoteur lancé à côté : Google, Bing, Qwant, Brave…)."""
    def fetch():
        import requests
        data = None
        for attempt in range(3):
            try:
                time.sleep(1.0)
                data = requests.get(f"{SEARXNG_URL}/search", timeout=30,
                                    params={"q": q, "format": "json", "language": "fr-FR"}).json()
            except Exception:
                data = None
            if isinstance(data, dict) and data.get("results"):
                break
            time.sleep(5)
        res = (data or {}).get("results", []) if isinstance(data, dict) else []
        return [{"url": r.get("url", ""), "title": r.get("title", ""), "snippet": r.get("content", ""),
                 "engines": r.get("engines", [])} for r in res][:n]
    return cached(json.dumps(["searxng", q], ensure_ascii=False), fetch)


def ddg_search(q: str, n: int = 8) -> list[dict]:
    def fetch():
        import requests
        for attempt in range(3):
            try:
                time.sleep(1.5)
                # En GET, DuckDuckGo répond par un captcha ; en POST, il passe.
                r = requests.post("https://lite.duckduckgo.com/lite/", data={"q": q}, timeout=20,
                                 headers={"User-Agent": "Mozilla/5.0"})  # un UA complet déclenche le captcha
                page = r.text
            except Exception:
                time.sleep(5)
                continue
            links = re.findall(r"""<a[^>]+href="([^"]+)"[^>]*class='result-link'>(.*?)</a>""", page, re.S)
            snippets = re.findall(r"<td class='result-snippet'>(.*?)</td>", page, re.S)
            if not links and ("anomaly" in page.lower() or "challenge" in page.lower()):
                time.sleep(30)
                continue
            clean = lambda s: html.unescape(re.sub(r"<[^>]+>", "", s)).strip()
            return [{"url": u, "title": clean(t), "snippet": clean(s)}
                    for (u, t), s in zip(links, snippets + [""] * len(links))][:n]
        return []
    return cached(json.dumps(["ddg2", q], ensure_ascii=False), fetch)


def B3_recherche_llm(model: str):
    def method(race):
        year = (race.get("date") or "")[:4]
        name = re.sub(r"\b(19|20)\d{2}\b", "", race["name"]).strip(" -")
        results = web_search(f"{name} {year} course lieu")
        if not results:
            return None
        listing = "\n".join(f"- {r['title']} — {r['snippet']} ({r['url']})" for r in results)
        prompt = (f"Course à pied en France : « {race['name']} », le {race['date']}.\n"
                  f"Résultats d'une recherche web :\n{listing}\n\n"
                  "D'après ces résultats SEULEMENT, dans quelle commune a lieu le départ ? "
                  f"Méfie-toi des homonymes (autre course, autre année).\n{_FORMAT}")
        ans = _parse_answer(llm(model, prompt))
        if not ans:
            return None
        town, dept, _ = ans
        r = resolve_commune(town, dept)
        return {**r, "why": f"recherche+modèle : {town} ({dept}) → {r['why']}"} if r else None
    return method


def _key_accents(text: str) -> str:
    """Comparaison qui garde les accents (Loire ≠ Loiré) mais ignore casse,
    tirets, apostrophes et un article en tête (L'Isle-Jourdain = Isle Jourdain)."""
    t = re.sub(r"[’'`\-\s]+", " ", (text or "").lower()).strip()
    t = re.sub(r"^(l|le|la|les) ", "", t)
    return t.replace(" ", "")


def B1b_commune_du_nom_strict(race):
    """Comme B1, mais seulement un nom placé en tête du nom de la course ou
    juste après « de », « d' » ou « à » (pas « de la », « du », « des » :
    ceux-là annoncent un nom commun : du Pont, de la Côte, des Ponts)."""
    name = re.sub(r"\b(19|20)\d{2}\b", " ", race["name"])
    name = re.sub(r"[’`]", "'", name)
    # Positions candidates : début du nom, ou après « de » / « d' » / « à » / « de l' ».
    starts = [0] + [m.end() for m in re.finditer(r"(?i)\b(?:de|à)\s+(?:l')?|\bd'", name)]
    for start in starts:
        rest = name[start:]
        tokens = [t for t in re.split(r"[\s,/()«»\"!?:;.]+", rest) if t][:5]
        for size in range(len(tokens), 0, -1):
            chunk = " ".join(tokens[:size]).strip("-' ")
            n = norm(chunk)
            if len(n) < 3 or all(w in _STOP for w in n.split()):
                continue
            want = _key_accents(expand(chunk))
            for q in {expand(chunk), expand(chunk).replace("-", " ")}:
                res = ban(q, type="municipality", limit=10)
                same = [r for r in res if _key_accents(r["city"]) == want]
                if len({r["citycode"] for r in same}) == 1:
                    r = same[0]
                    return {"lat": r["lat"], "lng": r["lng"],
                            "why": f"« {chunk} » = {r['city']} ({dept_of(r['citycode'])})"}
                if same:
                    break  # homonymes : on s'abstient pour ce morceau
    return None


# --- B4 : agent (modèle gratuit + outils : recherche, lecture de page, géocodeur) ---

def _page_text(html_text: str) -> str:
    t = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", html_text)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"\s+", " ", t).strip()


def fetch_page(url: str) -> dict:
    """Texte d'une page : HTTP simple, puis navigateur si la page est vide
    sans JavaScript. Renvoie {text, via}."""
    def get():
        import requests
        try:
            r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            text = _page_text(r.text) if r.ok else ""
        except Exception:
            text = ""
        if len(text) >= 600:
            return {"text": text[:20000], "via": "http"}
        import subprocess
        try:
            out = subprocess.run(["node", str(Path(__file__).with_name("render_page.mjs")), url],
                                 capture_output=True, text=True, timeout=45).stdout
        except Exception:
            out = ""
        return {"text": re.sub(r"\s+", " ", out)[:20000], "via": "navigateur"}
    return cached(json.dumps(["page", url]), get)


_LIEU = re.compile(r"(?i)lieu|départ|depart|adresse|commune|rendez|parking|salle|stade|\b\d{5}\b")


def _excerpt(text: str, limit: int = 3500) -> str:
    """Début de la page + passages qui parlent de lieu."""
    head = text[:1200]
    bits, used = [], 0
    for m in _LIEU.finditer(text[1200:]):
        a = max(0, m.start() + 1200 - 150)
        bits.append(text[a:a + 300])
        used += 300
        if used > limit - 1200:
            break
    return head + (" … " + " … ".join(bits) if bits else "")


TOOLS = [
    {"type": "function", "function": {
        "name": "recherche_web", "description": "Recherche web (DuckDuckGo). Renvoie titres, extraits et liens.",
        "parameters": {"type": "object", "properties": {"requete": {"type": "string"}}, "required": ["requete"]}}},
    {"type": "function", "function": {
        "name": "lire_page", "description": "Lit le texte d'une page web (avec un navigateur si besoin). Renvoie le début et les passages qui parlent du lieu.",
        "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {
        "name": "geocoder_commune", "description": "Vérifie une commune française dans la base officielle des adresses. Renvoie les communes correspondantes avec leur département et code postal.",
        "parameters": {"type": "object", "properties": {"nom": {"type": "string"}}, "required": ["nom"]}}},
]


def _run_tool(name: str, args: dict) -> str:
    if name == "recherche_web":
        res = web_search(args.get("requete", ""))
        return "\n".join(f"- {r['title']} — {r['snippet']} ({r['url']})" for r in res) or "aucun résultat"
    if name == "lire_page":
        page = fetch_page(args.get("url", ""))
        return _excerpt(page["text"]) or "page vide ou inaccessible"
    if name == "geocoder_commune":
        res = ban(expand(args.get("nom", "")), type="municipality", limit=5)
        return "\n".join(f"- {r['city']} ({dept_of(r['citycode'])}), CP {r['postcode']}, score {r['score']:.2f}"
                         for r in res) or "aucune commune"
    return "outil inconnu"


def _chat(model: str, messages: list) -> dict:
    """Un tour de conversation avec outils (mis en cache)."""
    def call():
        import requests
        for attempt in range(6):
            wait = 3.2 - (time.time() - _last_call[0])
            if wait > 0:
                time.sleep(wait)
            _last_call[0] = time.time()
            try:
                r = requests.post(OR_URL, timeout=180, headers={"Authorization": f"Bearer {_key()}"},
                                  json={"model": model, "temperature": 0, "messages": messages, "tools": TOOLS})
                d = r.json()
            except Exception:
                time.sleep(5)
                continue
            if "choices" in d:
                return {"message": d["choices"][0]["message"]}
            code = (d.get("error") or {}).get("code")
            if code in (429, 502, 503, 408) or r.status_code >= 500:
                time.sleep(10 * (attempt + 1))
                continue
            return {"error": d.get("error")}
        return {"error": "retries"}
    return cached(json.dumps(["chat", model, messages], ensure_ascii=False), call)


def B4_agent(model: str, max_steps: int = 8):
    def method(race):
        messages = [{"role": "user", "content": (
            f"Course à pied en France : « {race['name']} », le {race['date']}.\n"
            "Trouve la commune de départ. Utilise les outils : cherche sur le web, lis la page de "
            "l'organisateur ou d'un calendrier si besoin, puis vérifie la commune avec geocoder_commune. "
            "Méfie-toi des homonymes (autre course, autre année, autre commune du même nom).\n" + _FORMAT)}]
        steps = []
        for _ in range(max_steps):
            res = _chat(model, messages)
            msg = res.get("message")
            if not msg:
                return None
            calls = msg.get("tool_calls") or []
            if not calls:
                ans = _parse_answer(msg.get("content") or "")
                if not ans:
                    return None
                town, dept, src = ans
                r = resolve_commune(town, dept)
                if not r:
                    return None
                return {**r, "why": f"agent [{', '.join(steps)}] : {town} ({dept}) {src[:60]}"}
            messages.append({"role": "assistant", "content": msg.get("content") or "",
                             "tool_calls": calls})
            for c in calls:
                fn = c["function"]["name"]
                try:
                    args = json.loads(c["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                steps.append(fn.split("_")[0])
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": _run_tool(fn, args)[:4000]})
        return None
    return method
