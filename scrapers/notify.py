"""WhatsApp/Beeper notification for club races — monthly digest.

**Autonomous ``send``** (the active path, used by the Docker cron) —
self-contained, stdlib only. Once a month (cron run of the LAST day of the
month, so the picture of next month is as fresh as possible), it
posts ONE single message to the Beeper Desktop HTTP API (the T14 Desktop,
reachable over Tailscale): the digest of all upcoming races with members.
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

The older ``list``/``build``/``mark``/``clear`` commands drive a manual
MCP-based loop over ``data/notifications_queue.json``. **That queue is not
currently written by the pipeline**, so this path is dormant — kept only for a
possible future human/MCP-driven flow; ``send`` fully covers today's need.

CLI:
    python -m scrapers.notify list            # formatted messages + ids (JSON)
    python -m scrapers.notify build <id>      # message text for one race
    python -m scrapers.notify mark <id>...    # remove races from the queue
    python -m scrapers.notify clear           # empty the queue
    python -m scrapers.notify token           # obtain a token (accept popup on T14)
    python -m scrapers.notify send            # dry-run: print what WOULD be sent
    python -m scrapers.notify send --live      # post the digest if due this month
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
    TOKEN_REMINDER_DAYS  remind N days before expiry (default: 3)
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
NOTIFY_QUEUE_PATH = ROOT / "data" / "notifications_queue.json"
NOTIFIED_LOG_PATH = ROOT / "data" / "notified.json"

# "Blabla Run Event 86" WhatsApp group (Beeper MCP renderer id — NOT the API
# Matrix chatID). The autonomous sender uses BEEPER_CHAT_ID instead.
CLUB_CHAT_ID = "22548"

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
REMINDER_DAYS = int(os.environ.get("TOKEN_REMINDER_DAYS", "3"))
# Le rappel d'expiration va toujours à "Note to self" (perso), jamais au groupe.
REMINDER_CHAT_ID = os.environ.get(
    "BEEPER_REMINDER_CHAT_ID", ""
)
# Timeout de l'étape d'acceptation OAuth : si tu n'acceptes pas la popup, on
# abandonne proprement au lieu de bloquer.
OAUTH_ACCEPT_TIMEOUT = int(os.environ.get("OAUTH_ACCEPT_TIMEOUT", "60"))

_MAX_NAMES = 5
# Noms de mois pour l'en-tête du récap (index 1-12).
_MONTHS_FR = [
    "", "janvier", "février", "mars", "avril", "mai", "juin", "juillet",
    "août", "septembre", "octobre", "novembre", "décembre",
]


def _load_queue() -> list[dict]:
    if NOTIFY_QUEUE_PATH.exists():
        try:
            return json.loads(NOTIFY_QUEUE_PATH.read_text(encoding="utf-8")).get(
                "pending", []
            )
        except Exception:
            pass
    return []


def _save_queue(pending: list[dict]) -> None:
    NOTIFY_QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    NOTIFY_QUEUE_PATH.write_text(
        json.dumps({"pending": pending}, ensure_ascii=False, indent=2), encoding="utf-8"
    )


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


def build_message(item: dict) -> str:
    """Build the WhatsApp message text for one race."""
    name = item.get("name", "Course")
    date = _fmt_date(item.get("date", ""))
    location = item.get("location") or ""
    count = item.get("member_count", 0)
    names = _member_names(item)

    when = f"🗓️ {date}" + (f" — {location}" if location else "")

    if names:
        shown = names[:_MAX_NAMES]
        suffix = f" +{len(names) - _MAX_NAMES}" if len(names) > _MAX_NAMES else ""
        who = " : " + ", ".join(shown) + suffix
    else:
        who = ""

    plural = "s" if count > 1 else ""
    lines = [
        "🏁 Nouvelle course repérée pour le club !",
        "",
        f"📍 {name}",
        when,
        f"👥 {count} membre{plural} inscrit{plural}{who}",
    ]
    # Lien vers "Où court le club" (deep-link sur la course) ; le lien
    # d'inscription est accessible depuis la fiche de la course sur le site.
    race_id = item.get("id")
    site_link = f"{SITE_URL}#race/{race_id}" if race_id else SITE_URL
    lines += ["", f"🗺️ Où court le club : {site_link}"]
    lines += ["", "Qui d'autre y va ? 👀"]
    return "\n".join(lines)


def build_digest(races: list[dict]) -> str:
    """Le récap mensuel : UN message regroupant toutes les courses à venir
    avec des membres (demande de Maxime, Team com 2026-09-20). Tous les noms
    sont affichés, sans troncature — le message reste dans le groupe privé."""
    today = date.today()
    lines = [
        f"🗓️ Où court le club — récap de {_MONTHS_FR[today.month]} {today.year}",
        "",
    ]
    for r in races:
        name = r.get("name", "Course")
        when = _fmt_date(r.get("date", ""))
        location = r.get("location") or ""
        count = r.get("member_count", 0)
        names = _member_names(r)
        who = " : " + ", ".join(names) if names else ""
        plural = "s" if count > 1 else ""
        race_id = r.get("id")
        site_link = f"{SITE_URL}#race/{race_id}" if race_id else SITE_URL
        lines += [
            f"📍 {when} — {name}" + (f" ({location})" if location else ""),
            f"👥 {count} membre{plural} inscrit{plural}{who}",
            f"🗺️ {site_link}",
            "",
        ]
    lines.append("Qui d'autre y va ? 👀")
    return "\n".join(lines)


def cmd_list() -> int:
    pending = _load_queue()
    if not pending:
        print("Aucune notification en attente.")
        return 0
    print(f"# {len(pending)} notification(s) en attente — chat cible: {CLUB_CHAT_ID}\n")
    for item in pending:
        print(f"--- id: {item.get('id')} ---")
        print(build_message(item))
        print()
    # Machine-readable footer for tooling
    print("# ids:", json.dumps([i.get("id") for i in pending]))
    return 0


def cmd_build(race_id: str) -> int:
    for item in _load_queue():
        if item.get("id") == race_id:
            print(build_message(item))
            return 0
    print(f"id introuvable dans la file: {race_id}", file=sys.stderr)
    return 1


def cmd_mark(ids: list[str]) -> int:
    pending = _load_queue()
    targets = set(ids)
    kept = [i for i in pending if i.get("id") not in targets]
    sent = [i for i in pending if i.get("id") in targets]
    _save_queue(kept)

    # Append to a persistent log of what was already announced
    log = []
    if NOTIFIED_LOG_PATH.exists():
        try:
            log = json.loads(NOTIFIED_LOG_PATH.read_text(encoding="utf-8")).get(
                "notified", []
            )
        except Exception:
            log = []
    log.extend(i.get("id") for i in sent)
    NOTIFIED_LOG_PATH.write_text(
        json.dumps({"notified": log}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"{len(sent)} marquée(s) notifiée(s), {len(kept)} restante(s).")
    return 0


def cmd_clear() -> int:
    _save_queue([])
    print("File vidée.")
    return 0


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
        f"?name={GH_ARTIFACT_NAME}&per_page=1",
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
            return json.loads(zf.read(filename).decode("utf-8"))
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


def _load_digest_state() -> dict:
    """État persisté du récap : ``{"last_digest": "YYYY-MM"}`` ({} si absent)."""
    return _read_json(SEND_NOTIFIED_PATH, {})


def _save_digest_state(month: str) -> None:
    _write_json(SEND_NOTIFIED_PATH, {"last_digest": month})


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
    """Envoie UN message d'exemple (la prochaine course à venir) vers la cible,
    sans toucher au log de dédup. Sert à vérifier la chaîne de bout en bout."""
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
    msg = "🧪 TEST — " + build_message(eligible[0])
    print(f"Envoi test vers {BEEPER_CHAT_ID} …\n{msg}\n")
    ok, body = _post_message(token, BEEPER_CHAT_ID, msg)
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
    today = date.today()
    month = today.strftime("%Y-%m")
    last = _load_digest_state().get("last_digest")
    # Récap envoyé le DERNIER jour du mois (photo la plus fraîche possible du
    # mois à venir) : dû si on y est et qu'il n'a pas encore été envoyé.
    is_last_day = (today + timedelta(days=1)).month != today.month
    due = is_last_day and last != month

    # Token : réutilisé tant qu'il est valide (~30j). En LIVE on ne fait PAS de
    # fetch automatique (ça demanderait ton acceptation) : si le token manque ou
    # est expiré, on le signale et on s'arrête proprement.
    stored = _load_stored_token()
    days_left = (
        int((stored["expires_at"] - time.time()) // 86400) if stored else None
    )

    if due:
        status = "DÛ"
    elif last == month:
        status = "déjà envoyé"
    else:
        status = "prévu le dernier jour du mois"
    print(
        f"# {len(eligible)} course(s) à venir avec membres | "
        f"dernier récap: {last or 'jamais'} | récap {month}: {status}\n"
        f"# cible: {BEEPER_CHAT_ID} | mode: {'LIVE' if live else 'DRY-RUN'} | "
        f"token: {'valide ' + str(days_left) + 'j' if stored else 'ABSENT/EXPIRÉ'}"
    )

    # Rappel d'expiration (toujours vers Note to self), tant que le token vit.
    if live and stored and days_left is not None and days_left <= REMINDER_DAYS:
        ok, body = _post_message(
            stored["access_token"],
            REMINDER_CHAT_ID,
            _reminder_text(days_left, stored["expires_at"]),
        )
        print(
            f"\n🔔 Rappel expiration token ({days_left}j): "
            + ("envoyé" if ok else f"échec {body[:100]}")
        )

    if not due:
        if last == month:
            print("\n✅ Récap du mois déjà envoyé — rien à faire.")
        else:
            print("\n✅ Récap prévu le dernier jour du mois — rien à faire.")
        return 0

    if not eligible:
        # Mois sans course à annoncer : on marque quand même pour ne pas
        # ré-afficher ce bilan à chaque run quotidien.
        if live:
            _save_digest_state(month)
        print("\n✅ Aucune course à venir avec membres — pas de récap ce mois-ci.")
        return 0

    msg = build_digest(eligible)
    print(f"\n--- récap {month} ---\n{msg}")

    if not live:
        print(
            "\n(DRY-RUN) ce récap serait envoyé. Rien n'a été posté ni marqué. "
            "Ajoute --live pour envoyer."
        )
        return 0

    if not stored:
        print(
            "\n❌ Pas de token valide. Lance `notify.py token` et accepte la popup "
            "sur le T14 (rien n'a été envoyé ni marqué).",
            file=sys.stderr,
        )
        return 1

    ok, body = _post_message(stored["access_token"], BEEPER_CHAT_ID, msg)
    if not ok:
        # Mois non marqué → retentative au prochain run.
        print(f"\n❌ Échec envoi: {body[:150]}", file=sys.stderr)
        return 1
    _save_digest_state(month)
    print("\n✅ Récap envoyé.")
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        return cmd_list()
    cmd, rest = argv[0], argv[1:]
    if cmd == "list":
        return cmd_list()
    if cmd == "build" and rest:
        return cmd_build(rest[0])
    if cmd == "mark" and rest:
        return cmd_mark(rest)
    if cmd == "clear":
        return cmd_clear()
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
