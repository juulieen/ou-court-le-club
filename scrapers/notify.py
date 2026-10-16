"""WhatsApp/Beeper notification for club races — monthly digest.

**Autonomous ``send``** (the active path, used by the Docker cron) —
self-contained, stdlib only. Once a month (cron run of the LAST day of the
month, so the picture of next month is as fresh as possible; retried daily
during the first ``DIGEST_CATCHUP_DAYS`` days of the next month if that send
failed), it posts ONE short message to the Beeper Desktop HTTP API (the T14
Desktop, reachable over Tailscale): one line per race of the coming month,
later races reduced to a count, a single link to the map.
Member names come from the FULL private ``races.json`` (GitHub artifact, via
``GH_TOKEN``) so the recap names everyone; without a token it falls back to
the public ``races.json`` (opt-in first names only). Dry-run by default;
pass ``--live`` to actually send.

Contexte : demande de Maxime (Team com, 2026-09-20) — les notifications au
fil de l'eau (une par nouvelle course + une par inscrit) étaient trop
nombreuses et trop longues, et noyaient la conversation. Le récap mensuel
unique les remplace ; le mécanisme de correction d'homonymes par réaction 🚫
(qui exigeait un message par membre) est abandonné avec elles — les
exclusions se gèrent désormais à la main dans ``exclusions.json``.

CLI:
    python -m scrapers.notify token           # obtain a token (accept popup on T14)
    python -m scrapers.notify send            # dry-run: print what WOULD be sent
    python -m scrapers.notify send --live      # post the digest if due
    python -m scrapers.notify test             # digest preview → Note to self
    python -m scrapers.notify fetch-private    # drop races_private.json (full names)
                                               # into PRIVATE_DIR for the
                                               # tailnet-only courses.juulieen.fr

The Beeper token requires a **manual popup acceptance on the Desktop** each time
it is obtained, so it is fetched once via ``token`` and reused for ~30 days;
``send`` never fetches automatically — instead it posts an expiry reminder to
"Note to self" a few days before the stored token dies.

``send``/``token`` configuration (all via env, sensible defaults):
    RACES_URL          fallback races.json  (default: public GitHub Pages,
                       opt-in first names only)
    GH_TOKEN           fine-grained PAT (Actions: read) — enables the FULL
                       races.json (all member names) from the private
                       ``scraper-data`` workflow artifact
    GH_REPO            repo owning the artifact (default: juulieen/ou-court-le-club)
    BEEPER_API         Beeper Desktop API   (default: http://127.0.0.1:23373)
    BEEPER_CHAT_ID     target Matrix chatID (default: "Note to self" — SAFE)
    NOTIFIED_PATH      digest state path    (default: data/notified.json)
    PRIVATE_DIR        fetch-private target (served by the tailnet-only vhost)
    TOKEN_PATH         stored token path    (default: data/beeper_token.json)
    TOKEN_REMINDER_DAYS  remind daily from N days before expiry (default: 7)
    ALERT_URL          out-of-Beeper alert (e.g. https://ntfy.sh/<topic>) when
                       the token is expired or a due digest fails to send
    DIGEST_CATCHUP_DAYS  retry window into the announced month (default: 7)
"""

import base64
import hashlib
import io
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NOTIFIED_LOG_PATH = ROOT / "data" / "notified.json"

# --- Autonomous `send` config (env-overridable) ---
RACES_URL = os.environ.get(
    "RACES_URL", "https://juulieen.github.io/ou-court-le-club/data/races.json"
)
# Source privilégiée : le races.json COMPLET (noms de tous les membres),
# publié en artifact `scraper-data` à chaque run du workflow scrape (repo
# privé, rétention 7j, scrape quotidien donc toujours frais). Nécessite un
# PAT fine-grained (Actions: read) ; sans lui, fallback sur RACES_URL
# (prénoms opt-in uniquement).
GH_TOKEN = os.environ.get("GH_TOKEN", "")
GH_REPO = os.environ.get("GH_REPO", "juulieen/ou-court-le-club")
GH_ARTIFACT_NAME = os.environ.get("GH_ARTIFACT_NAME", "scraper-data")
# Dossier servi par le vhost tailnet-only courses.juulieen.fr : la commande
# `fetch-private` y dépose le races_private.json de l'artifact (noms complets).
PRIVATE_DIR = os.environ.get("PRIVATE_DIR", "")
BEEPER_API = os.environ.get("BEEPER_API", "http://127.0.0.1:23373").rstrip("/")
# Default target is "Note to self" so nothing lands in the club group by
# accident. Point BEEPER_CHAT_ID at the group's Matrix id once validated.
BEEPER_CHAT_ID = os.environ.get("BEEPER_CHAT_ID", "")
_NOTIFIED_ENV = os.environ.get("NOTIFIED_PATH")
SEND_NOTIFIED_PATH = Path(_NOTIFIED_ENV) if _NOTIFIED_ENV else NOTIFIED_LOG_PATH
# "Où court le club" — the public map. Messages link here (deep-link to the
# race via #race/<id>); the registration link lives on the race popup there.
SITE_URL = os.environ.get(
    "SITE_URL", "https://juulieen.github.io/ou-court-le-club/"
).rstrip("/") + "/"

# Le token OAuth du Desktop demande une acceptation manuelle (popup) sur le T14
# à chaque obtention. On le garde donc ~30 jours et on le réutilise ; un rappel
# est posté dans "Note to self" quelques jours avant l'expiration.
_TOKEN_ENV = os.environ.get("TOKEN_PATH")
TOKEN_PATH = Path(_TOKEN_ENV) if _TOKEN_ENV else (ROOT / "data" / "beeper_token.json")
REMINDER_DAYS = int(os.environ.get("TOKEN_REMINDER_DAYS", "7"))
# Alerte hors Beeper (ex. topic ntfy : https://ntfy.sh/<topic>) : POST du texte
# brut. Sert quand Beeper ne peut plus rien poster — token expiré, ou récap
# dû qui échoue. Vide = alertes seulement dans les logs du conteneur.
ALERT_URL = os.environ.get("ALERT_URL", "")
# Le récap du mois M+1 part le dernier jour de M ; s'il échoue (T14 éteint,
# token expiré…), on retente chaque jour jusqu'au CATCHUP_DAYS-ième jour de M+1.
CATCHUP_DAYS = int(os.environ.get("DIGEST_CATCHUP_DAYS", "7"))
# Le rappel d'expiration va toujours à "Note to self" (perso), jamais au groupe.
REMINDER_CHAT_ID = os.environ.get(
    "BEEPER_REMINDER_CHAT_ID", ""
)
# Timeout de l'étape d'acceptation OAuth : si tu n'acceptes pas la popup, on
# abandonne proprement au lieu de bloquer.
OAUTH_ACCEPT_TIMEOUT = int(os.environ.get("OAUTH_ACCEPT_TIMEOUT", "60"))

# Noms de mois pour l'en-tête du récap (index 1-12).
_MONTHS_FR = [
    "", "janvier", "février", "mars", "avril", "mai", "juin", "juillet",
    "août", "septembre", "octobre", "novembre", "décembre",
]
_WEEKDAYS_FR = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]
# Noms affichés par course dans le récap avant de passer à « +N ».
_DIGEST_MAX_NAMES = 3


def _fmt_date(iso: str) -> str:
    """YYYY-MM-DD -> DD/MM/YYYY (leave anything else untouched)."""
    parts = (iso or "")[:10].split("-")
    if len(parts) == 3 and all(parts):
        return f"{parts[2]}/{parts[1]}/{parts[0]}"
    return iso or "date à venir"


def _member_names(item: dict) -> list[str]:
    """Noms affichés dans les messages : noms complets (flux privé artifact)
    si disponibles, sinon prénoms opt-in (flux public)."""
    names = [m.get("name", "") for m in item.get("members") or [] if m.get("name")]
    return names or (item.get("first_names") or [])


def _display_race_name(name: str) -> str:
    """« LA MONTBRUNIGHT » → « La Montbrunight » ; les noms déjà en casse
    mixte sont laissés tels quels."""
    if not name.isupper():
        return name
    small = {"de", "du", "des", "la", "le", "les", "et", "en", "à", "au", "aux"}
    words = name.lower().split(" ")
    # .title() gère les traits d'union et apostrophes (« St-Julien », « L'Ars »).
    return " ".join(
        w if i and w in small else w.title() for i, w in enumerate(words)
    )


def _digest_line(r: dict) -> str:
    """• sam. 14 · Marathon du Cognac (Jarnac, Charente) — Lucas +3"""
    iso = (r.get("date") or "")[:10]
    try:
        d = date.fromisoformat(iso)
        when = f"{_WEEKDAYS_FR[d.weekday()]} {d.day}"
    except ValueError:
        when = _fmt_date(iso)
    name = _display_race_name(r.get("name") or "Course")
    location = r.get("location") or ""
    count = r.get("member_count", 0)
    names = _member_names(r)[:_DIGEST_MAX_NAMES]
    if names:
        rest = count - len(names)
        who = ", ".join(names) + (f" +{rest}" if rest > 0 else "")
    else:
        who = f"{count} membre" + ("s" if count > 1 else "")
    return f"• {when} · {name}" + (f" ({location})" if location else "") + f" — {who}"


def build_digest(races: list[dict], period: str) -> str:
    """Le récap mensuel : UN message court pour le mois ``period`` (YYYY-MM).
    Demande de Maxime (Team com, 2026-09-20) : les messages longs se perdaient
    dès que quelqu'un écrivait juste avant. Donc une ligne par course du mois
    annoncé, les courses plus lointaines réduites à un compte, un seul lien.
    ``races`` = courses à venir avec membres, triées par date."""
    year, month = (int(x) for x in period.split("-"))
    start = f"{period}-01"
    end = date(year + month // 12, month % 12 + 1, 1).isoformat()
    # Les courses d'avant le mois annoncé (celles du jour de l'envoi, le 31)
    # n'ont rien à faire sous le titre « novembre ».
    soon = [r for r in races if start <= (r.get("date") or "")[:10] < end]
    races = [r for r in races if (r.get("date") or "")[:10] >= start]
    later = len(races) - len(soon)

    lines = [f"🗓️ **Où court le club — {_MONTHS_FR[month]} {year}**", ""]
    if soon:
        lines += [_digest_line(r) for r in soon]
    else:
        lines.append(f"Aucune course repérée en {_MONTHS_FR[month]} pour l'instant.")
    if later:
        s = "s" if later > 1 else ""
        lines += ["", f"➕ {later} course{s} plus tard"]
    lines += ["", f"🗺️ Toutes les courses : {SITE_URL}", "Qui d'autre y va ? 👀"]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Autonomous `send` — fetch races.json, diff, post to the Beeper Desktop API
# --------------------------------------------------------------------------


def _http(method: str, url: str, *, headers=None, data=None, timeout=20):
    """Minimal HTTP helper. Returns (status, body_text)."""
    req = urllib.request.Request(url, method=method, headers=headers or {})
    if data is not None:
        req.data = data
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        # Réseau (T14 éteint, timeout, DNS…) → status 0, traité comme un échec
        # par tous les appelants (pas de crash).
        return 0, str(e)


def _read_json(path: Path, default):
    """Read a JSON file, returning `default` on any error (missing/corrupt)."""
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return default


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_stored_token() -> dict | None:
    """Return the stored token dict if still valid, else None."""
    d = _read_json(TOKEN_PATH, None)
    if d and d.get("access_token") and d.get("expires_at", 0) > time.time() + 60:
        return d
    return None


def _store_token(access_token: str, expires_in: int) -> dict:
    d = {
        "access_token": access_token,
        "expires_at": int(time.time()) + int(expires_in),
        "obtained_at": int(time.time()),
    }
    _write_json(TOKEN_PATH, d)
    return d


def _require_token() -> str | None:
    """Return a valid stored access token, or print the fix hint and return None."""
    stored = _load_stored_token()
    if not stored:
        print(
            "❌ Pas de token valide. Lance `notify.py token` et accepte la popup.",
            file=sys.stderr,
        )
        return None
    return stored["access_token"]


def _fetch_token() -> dict:
    """Run the OAuth PKCE flow against the Beeper Desktop API and store the
    token. **Requires the user to accept the popup on the Desktop** — the
    authorize step waits up to OAUTH_ACCEPT_TIMEOUT then gives up cleanly."""
    # 1. register a public client
    st, body = _http(
        "POST",
        f"{BEEPER_API}/oauth/register",
        headers={"Content-Type": "application/json"},
        data=json.dumps(
            {
                "client_name": "runevent86-notify",
                "redirect_uris": ["http://127.0.0.1:19999/callback"],
                "grant_types": ["authorization_code"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            }
        ).encode(),
    )
    if st not in (200, 201):
        raise RuntimeError(f"oauth register failed ({st}): {body[:200]}")
    client_id = json.loads(body)["client_id"]

    # 2. PKCE pair
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )

    # 3. authorize -> code (BLOCKS until you accept the popup on the Desktop)
    st, body = _http(
        "POST",
        f"{BEEPER_API}/oauth/authorize/callback",
        headers={"Content-Type": "application/json"},
        data=json.dumps(
            {
                "mode": "oauth2",
                "clientInfo": {"name": "runevent86-notify", "clientID": client_id},
                "scopes": ["read", "write"],
                "redirectUri": "http://127.0.0.1:19999/callback",
                "scope": "read write",
                "codeChallenge": challenge,
                "codeChallengeMethod": "S256",
            }
        ).encode(),
        timeout=OAUTH_ACCEPT_TIMEOUT,
    )
    if st != 200:
        raise RuntimeError(f"oauth authorize failed ({st}): {body[:200]}")
    code = json.loads(body)["code"]

    # 4. exchange code -> token
    st, body = _http(
        "POST",
        f"{BEEPER_API}/oauth/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=urllib.parse.urlencode(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": "http://127.0.0.1:19999/callback",
                "client_id": client_id,
                "code_verifier": verifier,
            }
        ).encode(),
    )
    if st != 200:
        raise RuntimeError(f"oauth token failed ({st}): {body[:200]}")
    tok = json.loads(body)
    return _store_token(tok["access_token"], tok.get("expires_in", 2592000))


def _post_message(token: str, chat_id: str, text: str) -> tuple[bool, str]:
    st, body = _http(
        "POST",
        f"{BEEPER_API}/v1/chats/{urllib.parse.quote(chat_id, safe='')}/messages",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        data=json.dumps({"text": text}).encode(),
        timeout=30,
    )
    return st in (200, 201), body


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Bloque le suivi automatique des redirections (géré à la main pour ne
    pas fuiter le header Authorization vers l'URL signée Azure)."""

    def redirect_request(self, *args, **kwargs):
        return None


def _download_artifact_json(filename: str) -> dict | None:
    """Download the latest `scraper-data` artifact zip and return the parsed
    JSON of `filename` inside it. None if GH_TOKEN is unset or on any failure
    (callers fall back / retry at the next run)."""
    if not GH_TOKEN:
        return None
    headers = {
        "Authorization": f"Bearer {GH_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    st, body = _http(
        "GET",
        f"https://api.github.com/repos/{GH_REPO}/actions/artifacts"
        f"?name={GH_ARTIFACT_NAME}&per_page=10",
        headers=headers,
        timeout=20,
    )
    if st != 200:
        print(f"⚠️ artifacts GitHub: HTTP {st}", file=sys.stderr)
        return None
    artifacts = [
        a for a in json.loads(body).get("artifacts", []) if not a.get("expired")
    ]
    if not artifacts:
        print("⚠️ aucun artifact scraper-data disponible", file=sys.stderr)
        return None
    # L'API ne garantit pas l'ordre (vu : un artifact plus récent listé en
    # 2e) — on trie explicitement par date de création décroissante.
    artifacts.sort(key=lambda a: a.get("created_at", ""), reverse=True)
    url = (
        f"https://api.github.com/repos/{GH_REPO}"
        f"/actions/artifacts/{artifacts[0]['id']}/zip"
    )
    try:
        # L'endpoint /zip renvoie une 302 vers une URL signée (blob Azure) :
        # on lit la redirection à la main pour NE PAS renvoyer le header
        # Authorization au blob — il répondrait 401 « failed to authenticate ».
        req = urllib.request.Request(url, headers=headers)
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(req, timeout=60) as resp:
                blob = resp.read()
        except urllib.error.HTTPError as e:
            if e.code not in (301, 302, 303, 307, 308) or not e.headers.get("Location"):
                raise
            with urllib.request.urlopen(e.headers["Location"], timeout=60) as resp:
                blob = resp.read()
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            # upload-artifact v4 aplatit les chemins sur leur ancêtre commun
            # (tout est sous data/ → le zip contient les fichiers à plat).
            try:
                raw = zf.read(filename)
            except KeyError:
                raw = zf.read(Path(filename).name)
            return json.loads(raw.decode("utf-8"))
    except Exception as e:
        print(f"⚠️ téléchargement artifact ({filename}): {e}", file=sys.stderr)
        return None


def _fetch_races_full() -> list[dict] | None:
    """Le races.json COMPLET (noms de tous les membres) depuis l'artifact
    `scraper-data` du workflow scrape. None si non configuré ou indisponible
    (le caller retombe alors sur le flux public, prénoms opt-in seulement)."""
    data = _download_artifact_json("data/races.json")
    return data.get("races", []) if data else None


def _fetch_races() -> list[dict]:
    full = _fetch_races_full()
    if full is not None:
        print(f"# source: artifact {GH_ARTIFACT_NAME} (noms complets)")
        return full
    print(f"# source: {RACES_URL} (prénoms opt-in)")
    if RACES_URL.startswith(("http://", "https://")):
        st, body = _http("GET", RACES_URL, timeout=30)
        if st != 200:
            raise RuntimeError(f"fetch races.json failed ({st})")
        data = json.loads(body)
    else:
        data = json.loads(Path(RACES_URL).read_text(encoding="utf-8"))
    return data.get("races", [])


def _next_month(ym: str) -> str:
    y, m = (int(x) for x in ym.split("-"))
    return f"{y + m // 12}-{m % 12 + 1:02d}"


def _last_period() -> str | None:
    """Dernier mois ANNONCÉ par un récap (``YYYY-MM``), ou None.
    L'ancien état ``last_digest`` stockait le mois d'ENVOI (récap envoyé fin
    août = septembre annoncé) : converti à la lecture."""
    state = _read_json(SEND_NOTIFIED_PATH, {})
    if state.get("last_period"):
        return state["last_period"]
    if state.get("last_digest"):
        return _next_month(state["last_digest"])
    return None


def _save_state(**values) -> None:
    state = _read_json(SEND_NOTIFIED_PATH, {})
    state.update(values)
    _write_json(SEND_NOTIFIED_PATH, state)


def _done_today(key: str) -> bool:
    """Le cron tourne 3 fois par jour : rappels et alertes de token ne doivent
    partir qu'une fois. ``key`` note le jour du dernier envoi réussi."""
    return _read_json(SEND_NOTIFIED_PATH, {}).get(key) == date.today().isoformat()


def _mark_today(key: str) -> None:
    _save_state(**{key: date.today().isoformat()})


def _digest_period(today: date) -> str | None:
    """Mois que le récap doit annoncer aujourd'hui, ou None hors fenêtre.
    Fenêtre : le dernier jour du mois (annonce le mois suivant), puis les
    CATCHUP_DAYS premiers jours du mois (rattrapage si l'envoi a échoué)."""
    tomorrow = today + timedelta(days=1)
    if tomorrow.month != today.month:
        return tomorrow.strftime("%Y-%m")
    if today.day <= CATCHUP_DAYS:
        return today.strftime("%Y-%m")
    return None


def _alert(text: str) -> None:
    """Alerte hors Beeper (ALERT_URL, ex. ntfy). Toujours loguée sur stderr."""
    print(f"\n🚨 {text}", file=sys.stderr)
    if not ALERT_URL:
        return
    st, body = _http(
        "POST",
        ALERT_URL,
        headers={"Title": "RunEvent86 notify", "Tags": "warning"},
        data=text.encode("utf-8"),
    )
    if st not in (200, 201, 202, 204):
        print(f"   (alerte non délivrée : {st} {body[:100]})", file=sys.stderr)


def _eligible_upcoming(races: list[dict]) -> list[dict]:
    """Races with an id and members, happening today or later, sorted by date.
    (An id is required for dedup and for the #race/<id> link — skip any race
    without one so downstream `r["id"]` accesses are always safe.)"""
    today = date.today().isoformat()
    out = [
        r
        for r in races
        if r.get("id")
        and r.get("member_count", 0) > 0
        and (r.get("date") or "")[:10] >= today
    ]
    return sorted(out, key=lambda r: (r.get("date") or ""))


def _reminder_text(days_left: int, expires_at: int) -> str:
    when = datetime.fromtimestamp(expires_at, timezone.utc).strftime("%d/%m/%Y")
    return (
        "🔑 Rappel RunEvent86 — le token Beeper des notifs expire "
        f"dans {days_left} jour(s) (le {when}).\n"
        "Relance l'obtention du token et **accepte la popup sur le T14** :\n"
        "`docker exec -it runevent86-notify python /app/notify.py token`"
    )


def cmd_token(argv: list[str]) -> int:
    """Obtain (and store) a Beeper token — you must accept the popup on the T14."""
    print(
        f"Obtention d'un token via {BEEPER_API} …\n"
        "👉 Accepte la popup d'autorisation sur Beeper (T14) "
        f"(sinon abandon après {OAUTH_ACCEPT_TIMEOUT}s)."
    )
    try:
        d = _fetch_token()
    except Exception as e:
        print(f"❌ Échec: {e}", file=sys.stderr)
        return 1
    when = datetime.fromtimestamp(d["expires_at"], timezone.utc).strftime("%d/%m/%Y")
    print(f"✅ Token stocké dans {TOKEN_PATH} — valide jusqu'au {when}.")
    return 0


def cmd_test(argv: list[str]) -> int:
    """Envoie le récap du mois prochain, préfixé 🧪, sans rien marquer. Part
    vers la conversation des rappels (Note to self), jamais vers le groupe."""
    if not REMINDER_CHAT_ID:
        print(
            "❌ BEEPER_REMINDER_CHAT_ID vide : le test partirait dans le groupe.",
            file=sys.stderr,
        )
        return 1
    token = _require_token()
    if not token:
        return 1
    try:
        eligible = _eligible_upcoming(_fetch_races())
    except Exception as e:
        print(f"❌ Lecture races.json: {e}", file=sys.stderr)
        return 1
    if not eligible:
        print("Aucune course à venir avec membres à utiliser comme exemple.")
        return 1
    period = _next_month(date.today().strftime("%Y-%m"))
    msg = "🧪 TEST — " + build_digest(eligible, period)
    print(f"Envoi test vers {REMINDER_CHAT_ID} …\n{msg}\n")
    ok, body = _post_message(token, REMINDER_CHAT_ID, msg)
    print("✅ envoyé (rien marqué)" if ok else f"❌ échec: {body[:150]}")
    return 0 if ok else 1


def cmd_fetch_private(argv: list[str]) -> int:
    """Dépose le races_private.json (noms complets) de l'artifact scraper-data
    dans PRIVATE_DIR/races.json — servi par le vhost tailnet-only
    courses.juulieen.fr. Écriture atomique ; en cas d'échec, le fichier
    précédent est conservé (le site garde la dernière version valide)."""
    if not PRIVATE_DIR:
        print("❌ PRIVATE_DIR non défini.", file=sys.stderr)
        return 1
    if not GH_TOKEN:
        print("❌ GH_TOKEN requis (PAT fine-grained, Actions: read).", file=sys.stderr)
        return 1
    data = _download_artifact_json("data/races_private.json")
    if data is None:
        return 1
    out = Path(PRIVATE_DIR) / "races.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(out)
    print(f"✅ {len(data.get('races', []))} courses (noms complets) → {out}")
    return 0


def cmd_send(argv: list[str]) -> int:
    live = "--live" in argv or "--send" in argv

    try:
        races = _fetch_races()
    except Exception as e:
        print(f"❌ Impossible de lire races.json: {e}", file=sys.stderr)
        return 1

    eligible = _eligible_upcoming(races)
    period = _digest_period(date.today())
    last = _last_period()
    due = period is not None and last != period

    # Token : réutilisé tant qu'il est valide (~30j). En LIVE on ne fait PAS de
    # fetch automatique (ça demanderait ton acceptation) : si le token manque ou
    # est expiré, on alerte (hors Beeper, puisqu'il ne peut plus rien poster).
    stored = _load_stored_token()
    days_left = (
        int((stored["expires_at"] - time.time()) // 86400) if stored else None
    )

    if due:
        status = f"DÛ ({period})"
    elif period:
        status = f"{period} déjà envoyé"
    else:
        status = "hors fenêtre (dernier jour du mois)"
    print(
        f"# {len(eligible)} course(s) à venir avec membres | "
        f"dernier mois annoncé: {last or 'jamais'} | récap: {status}\n"
        f"# cible: {BEEPER_CHAT_ID} | mode: {'LIVE' if live else 'DRY-RUN'} | "
        f"token: {'valide ' + str(days_left) + 'j' if stored else 'ABSENT/EXPIRÉ'}"
    )

    if live and not stored and not _done_today("token_alerted"):
        # Répétée chaque jour jusqu'au renouvellement.
        _alert(
            "Token Beeper expiré : plus aucun récap ne peut partir. "
            "Relance `docker exec -it runevent86-notify python /app/notify.py "
            "token` et accepte la popup sur le T14."
        )
        _mark_today("token_alerted")
    elif (
        live
        and days_left is not None
        and days_left <= REMINDER_DAYS
        and not _done_today("token_reminded")
    ):
        # Rappel quotidien (Note to self) tant que le token vit encore.
        ok, body = _post_message(
            stored["access_token"],
            REMINDER_CHAT_ID,
            _reminder_text(days_left, stored["expires_at"]),
        )
        print(
            f"\n🔔 Rappel expiration token ({days_left}j): "
            + ("envoyé" if ok else f"échec {body[:100]}")
        )
        if ok:
            _mark_today("token_reminded")

    if not due:
        print("\n✅ Rien à envoyer aujourd'hui.")
        return 0

    if not eligible:
        # Mois sans course à annoncer : on marque quand même pour ne pas
        # ré-afficher ce bilan à chaque run de la fenêtre.
        if live:
            _save_state(last_period=period)
        print("\n✅ Aucune course à venir avec membres — pas de récap ce mois-ci.")
        return 0

    msg = build_digest(eligible, period)
    print(f"\n--- récap {period} ---\n{msg}")

    if not live:
        print(
            "\n(DRY-RUN) ce récap serait envoyé. Rien n'a été posté ni marqué. "
            "Ajoute --live pour envoyer."
        )
        return 0

    if not stored:
        # L'alerte token est déjà partie plus haut ; retentative au prochain run.
        return 1

    ok, body = _post_message(stored["access_token"], BEEPER_CHAT_ID, msg)
    if not ok:
        # Période non marquée → retentative au prochain run de la fenêtre.
        _alert(f"Échec d'envoi du récap {period} (nouvel essai au prochain run : 18h, 21h ou demain) : {body[:150]}")
        return 1
    _save_state(last_period=period)
    print("\n✅ Récap envoyé.")
    return 0


def main(argv: list[str]) -> int:
    cmd, rest = (argv[0], argv[1:]) if argv else ("", [])
    if cmd == "send":
        return cmd_send(rest)
    if cmd == "token":
        return cmd_token(rest)
    if cmd == "test":
        return cmd_test(rest)
    if cmd == "fetch-private":
        return cmd_fetch_private(rest)
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
