"""WhatsApp/Beeper notification for newly-detected club races.

**Autonomous ``send``** (the active path, used by the Docker cron) —
self-contained, stdlib only. It fetches the public ``races.json``, diffs it
against a persistent ``notified.json``, and posts straight to the Beeper
Desktop HTTP API (the T14 Desktop, reachable over Tailscale):
- each *new upcoming* race (full announcement), and
- each *new registration* on an already-known race (member_count increase,
  detected via a per-race snapshot stored in ``notified.json``) — **one
  message per newly-joined member**, named only if they are in
  ``display_optin`` (so a 🚫 reaction always targets a single person), plus
  one grouped message for the anonymous remainder.
Dry-run by default; pass ``--live`` to actually send. This is what
runs daily after the scrape, in a container on the ASUS.

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
    python -m scrapers.notify send --live      # actually post to Beeper

The Beeper token requires a **manual popup acceptance on the Desktop** each time
it is obtained, so it is fetched once via ``token`` and reused for ~30 days;
``send`` never fetches automatically — instead it posts an expiry reminder to
"Note to self" a few days before the stored token dies.

``send``/``token`` configuration (all via env, sensible defaults):
    RACES_URL          source of races.json (default: public GitHub Pages)
    BEEPER_API         Beeper Desktop API   (default: http://127.0.0.1:23373)
    BEEPER_CHAT_ID     target Matrix chatID (default: "Note to self" — SAFE)
    NOTIFIED_PATH      dedup log path       (default: data/notified.json)
    TOKEN_PATH         stored token path    (default: data/beeper_token.json)
    TOKEN_REMINDER_DAYS  remind N days before expiry (default: 3)
"""

import base64
import hashlib
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
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

# Correction des homonymes : un membre réagit avec cet emoji sur une notif →
# le membre cité par le message est masqué de la carte (PAS la course entière :
# les autres inscrits restent visibles). Si le message ne désigne pas une seule
# personne (plusieurs noms, membre anonyme), rien n'est masqué et une demande
# de précision est postée dans le chat. L'id de course et le prénom sont lus
# depuis le message lui-même (lien #race/<id>, ligne 🎉/👥) — aucune identité
# de réacteur n'est stockée.
HIDE_EMOJI = os.environ.get("HIDE_EMOJI", "🚫")
_EXCL_ENV = os.environ.get("EXCLUSIONS_PATH")
EXCLUSIONS_PATH = Path(_EXCL_ENV) if _EXCL_ENV else (ROOT / "data" / "exclusions.json")
# Nombre de messages récents scannés pour les réactions (couvre largement).
REACTIONS_SCAN = int(os.environ.get("REACTIONS_SCAN", "300"))

_MAX_NAMES = 5
# Id de course dans un lien #race/<id> — jusqu'au prochain espace/parenthèse,
# pour tolérer les ids exotiques.
_RACE_LINK_RE = re.compile(r"#race/([^\s)]+)")


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


def _hide_hint() -> str:
    """Ligne d'aide pour la correction des homonymes par réaction."""
    return (
        f"👉 Un homonyme s'est glissé dans cette notif (pas un membre du club) ? "
        f"Réagis avec {HIDE_EMOJI} à ce message pour le retirer de la carte."
    )


def build_message(item: dict) -> str:
    """Build the WhatsApp message text for one race."""
    name = item.get("name", "Course")
    date = _fmt_date(item.get("date", ""))
    location = item.get("location") or ""
    count = item.get("member_count", 0)
    names = item.get("first_names") or []

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
    # Auto-correction des homonymes : réagir masque le membre de la carte.
    lines += [_hide_hint()]
    return "\n".join(lines)


def _join_lines(item: dict) -> list[str]:
    """Lignes communes aux messages de nouvelle inscription."""
    name = item.get("name", "Course")
    date = _fmt_date(item.get("date", ""))
    location = item.get("location") or ""
    count = item.get("member_count", 0)
    names = item.get("first_names") or []

    when = f"🗓️ {date}" + (f" — {location}" if location else "")
    if names:
        shown = names[:_MAX_NAMES]
        suffix = f" +{len(names) - _MAX_NAMES}" if len(names) > _MAX_NAMES else ""
        who = " : " + ", ".join(shown) + suffix
    else:
        who = ""
    plural = "s" if count > 1 else ""

    race_id = item.get("id")
    site_link = f"{SITE_URL}#race/{race_id}" if race_id else SITE_URL
    return [
        "🏃 Nouvel inscrit repéré pour le club !",
        "",
        f"📍 {name}",
        when,
        "{who_line}",
        f"👥 {count} membre{plural} inscrit{plural}{who}",
        "",
        f"🗺️ Où court le club : {site_link}",
        _hide_hint(),
    ]


def build_join_message(item: dict, name: str) -> str:
    """Message pour UN nouvel inscrit nommé (opt-in) sur une course connue.
    Un message par inscription : un 🚫 vise ainsi toujours une seule personne."""
    lines = _join_lines(item)
    lines[lines.index("{who_line}")] = f"🎉 {name} a rejoint la course !"
    return "\n".join(lines)


def build_anon_join_message(item: dict, added: int) -> str:
    """Message pour de nouveaux inscrits non opt-in (prénom non public)."""
    lines = _join_lines(item)
    lines[lines.index("{who_line}")] = (
        f"➕ {added} nouveaux membres (prénoms non publics)"
        if added > 1
        else "➕ 1 nouveau membre (prénom non public)"
    )
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


def _fetch_races() -> list[dict]:
    if RACES_URL.startswith(("http://", "https://")):
        st, body = _http("GET", RACES_URL, timeout=30)
        if st != 200:
            raise RuntimeError(f"fetch races.json failed ({st})")
        data = json.loads(body)
    else:
        data = json.loads(Path(RACES_URL).read_text(encoding="utf-8"))
    return data.get("races", [])


def _snapshot(races: list[dict]) -> dict:
    """Per-race state used to detect new registrations on known races."""
    return {
        r["id"]: {"count": r.get("member_count", 0), "names": r.get("first_names") or []}
        for r in races
    }


def _save_notified_ids(ids: set[str], races_state: dict) -> None:
    _write_json(
        SEND_NOTIFIED_PATH, {"notified": sorted(ids), "races": races_state}
    )


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


def _extract_race_id(text: str) -> str | None:
    """Pull the race id out of a message's '#race/<id>' link."""
    m = _RACE_LINK_RE.search(text or "")
    return m.group(1) if m else None


def _norm_emoji(s: str) -> str:
    """Retire le variation selector (U+FE0F) et les espaces pour comparer les
    réactions de façon robuste entre réseaux (WhatsApp encode parfois 🚫️)."""
    return (s or "").replace("️", "").strip()


def _list_my_messages(token: str, chat_id: str, limit: int) -> list[dict] | None:
    """Fetch up to `limit` recent messages of a chat (paginated). Returns None
    if NOTHING could be read (T14 injoignable, token invalide…) so the caller
    can avoid clobbering state on a transient failure — distinct from a valid
    empty chat ([])."""
    out: list[dict] = []
    cursor = None
    got_page = False
    base = f"{BEEPER_API}/v1/chats/{urllib.parse.quote(chat_id, safe='')}/messages"
    while len(out) < limit:
        url = base + (f"?cursor={urllib.parse.quote(cursor)}" if cursor else "")
        st, body = _http(
            "GET", url, headers={"Authorization": f"Bearer {token}"}, timeout=20
        )
        if st != 200:
            break
        got_page = True
        try:
            data = json.loads(body)
        except Exception:
            break
        items = data.get("items", [])
        if not items:
            break
        out.extend(items)
        cursor = data.get("oldestCursor") or data.get("newestCursor")
        if not cursor or not data.get("hasMore", False):
            break
    return out[:limit] if got_page else None


def _extract_member(text: str) -> str | None:
    """Prénom affiché du membre visé par le message, None si ambigu.
    - message de nouvel inscrit : « 🎉 X a rejoint la course ! » (un par message)
    - annonce de course à UN seul inscrit nommé : « 👥 1 membre inscrit : X »
    Tout le reste (plusieurs noms, membre anonyme, ancien format groupé
    « X, Y ont rejoint ») est ambigu : le cron demandera plutôt que masquer.
    Note : le prénom est celui affiché au moment du message ; si la
    désambiguïsation évolue ensuite (« Romain » → « Romain F. »), l'exclusion
    cesse silencieusement de matcher (le membre réapparaît)."""
    m = re.search(r"🎉 (.+?) a rejoint la course", text or "")
    if m:
        return m.group(1).strip()
    m = re.search(r"👥 1 membre inscrit : ([^\n]+)", text or "")
    if m:
        who = m.group(1).strip()
        if "," not in who and "+" not in who:
            return who
    return None


def _load_exclusions() -> tuple[set[tuple[str, str]], set[str]]:
    """Retourne (membres masqués {(race, name)}, courses déjà demandées)."""
    d = _read_json(EXCLUSIONS_PATH, {})
    members = {
        (e.get("race", ""), e.get("name", ""))
        for e in d.get("members", [])
        if e.get("race") and e.get("name")
    }
    return members, set(d.get("asked", []))


def _save_exclusions(members: set[tuple[str, str]], asked: set[str]) -> None:
    _write_json(
        EXCLUSIONS_PATH,
        {
            "members": [
                {"race": rid, "name": name} for rid, name in sorted(members)
            ],
            "asked": sorted(asked),
            "updated_at": int(time.time()),
        },
    )


def cmd_reactions(argv: list[str]) -> int:
    """Scan the notification chat for HIDE_EMOJI reactions and reconcile
    exclusions.json (member-level). No identity is stored: the race id and
    the member's display name come from the message itself.

    Réconciliation (et non recalcul total) pour être robuste :
      - lecture impossible (T14 injoignable) → on ne touche à rien ;
      - un membre déjà masqué dont le message est hors de la fenêtre de scan
        (ou non lu ce run) reste masqué ;
      - on ne ré-affiche un membre QUE si on a vu son message SANS le 🚫
        (réaction réellement retirée) ;
      - 🚫 sur un message ambigu (plusieurs noms, membre anonyme) → rien de
        masqué, une demande de précision est postée (une seule fois)."""
    token = _require_token()
    if not token:
        return 1
    msgs = _list_my_messages(token, BEEPER_CHAT_ID, REACTIONS_SCAN)
    if msgs is None:
        print(
            "⚠️ Lecture des messages impossible (T14 injoignable ?) — "
            "exclusions inchangées.",
            file=sys.stderr,
        )
        return 1

    target = _norm_emoji(HIDE_EMOJI)
    seen_key: set[tuple[str, str]] = set()  # membre vu dans un message ce run
    seen_with: set[tuple[str, str]] = set()  # membre dont un message porte le 🚫
    ambiguous_with: set[str] = set()  # courses avec un 🚫 sur message ambigu
    for m in msgs:
        text = m.get("text", "")
        rid = _extract_race_id(text)
        if not rid:
            continue
        has_hide = any(
            _norm_emoji(r.get("reactionKey", "")) == target
            for r in (m.get("reactions") or [])
        )
        name = _extract_member(text)
        if name is None:
            if has_hide:
                ambiguous_with.add(rid)
            continue
        key = (rid, name)
        seen_key.add(key)
        if has_hide:
            seen_with.add(key)

    before, asked = _load_exclusions()
    # Ajoute les 🚫 vus, garde l'existant pour les membres non vus ce run, et
    # retire ceux vus SANS 🚫 (réaction enlevée). `seen_with ⊆ seen_key`.
    excluded = seen_with | (before - seen_key)

    # 🚫 ambigu : demande de précision postée UNE fois par course (sinon le
    # cron 2h spammerait). Seules les demandes réellement postées sont marquées.
    newly_asked: set[str] = set()
    for rid in sorted(ambiguous_with - asked):
        ok, body = _post_message(
            token,
            BEEPER_CHAT_ID,
            (
                f"{HIDE_EMOJI} bien vu — mais cette notif cite plusieurs membres "
                "(ou un membre non identifié publiquement) : je ne sais pas "
                "lequel retirer de la carte. Julien, à exclure à la main ?\n"
                f"{SITE_URL}#race/{rid}"
            ),
        )
        if ok:
            newly_asked.add(rid)
            print(f"  ? demande de précision postée pour {rid}")
        else:
            print(f"  ❌ échec demande pour {rid}: {body[:120]}", file=sys.stderr)

    _save_exclusions(excluded, asked | newly_asked)
    added = excluded - before
    removed = before - excluded
    print(
        f"# {len(msgs)} message(s) scanné(s) | emoji: {HIDE_EMOJI} | "
        f"{len(excluded)} membre(s) masqué(s)"
    )
    if added:
        print("  + masqués:", ", ".join(f"{n} ({r})" for r, n in sorted(added)))
    if removed:
        print("  - ré-affichés:", ", ".join(f"{n} ({r})" for r, n in sorted(removed)))
    if not added and not removed:
        print("  (aucun changement)")
    print(f"→ {EXCLUSIONS_PATH}")
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


def cmd_send(argv: list[str]) -> int:
    live = "--live" in argv or "--send" in argv
    seed_send = "--send-baseline" in argv  # notify even on first run

    try:
        races = _fetch_races()
    except Exception as e:
        print(f"❌ Impossible de lire races.json: {e}", file=sys.stderr)
        return 1

    eligible = _eligible_upcoming(races)
    notified = _read_json(SEND_NOTIFIED_PATH, None)  # None ⇒ premier run
    first_run = notified is None
    known = set(notified.get("notified", [])) if notified else set()
    new = [r for r in eligible if r["id"] not in known]

    # Nouvelles inscriptions sur des courses déjà connues : member_count en
    # hausse vs le snapshot persisté. Les courses sans snapshot (état écrit
    # avant cette feature) sont enregistrées silencieusement — pas de spam.
    state = (notified or {}).get("races", {})
    joins = []
    for r in eligible:
        if r["id"] not in known:
            continue
        prev = state.get(r["id"])
        if prev is None:
            continue
        cur = r.get("member_count", 0)
        prev_count = int(prev.get("count", 0))
        if cur > prev_count:
            prev_names = set(prev.get("names") or [])
            joined = [n for n in (r.get("first_names") or []) if n not in prev_names]
            joins.append((r, cur - prev_count, joined))

    # Token : réutilisé tant qu'il est valide (~30j). En LIVE on ne fait PAS de
    # fetch automatique (ça demanderait ton acceptation) : si le token manque ou
    # est expiré, on le signale et on s'arrête proprement.
    stored = _load_stored_token()
    days_left = (
        int((stored["expires_at"] - time.time()) // 86400) if stored else None
    )

    print(
        f"# source: {RACES_URL}\n"
        f"# {len(eligible)} course(s) à venir avec membres | "
        f"{len(known)} déjà notifiée(s) | {len(new)} nouvelle(s) course(s) | "
        f"{len(joins)} nouvelle(s) inscription(s)\n"
        f"# cible: {BEEPER_CHAT_ID} | mode: {'LIVE' if live else 'DRY-RUN'} | "
        f"token: {'valide ' + str(days_left) + 'j' if stored else 'ABSENT/EXPIRÉ'}"
    )

    if first_run and not seed_send:
        _save_notified_ids({r["id"] for r in eligible}, _snapshot(eligible))
        print(
            f"\n⚠️  Premier run — baseline enregistrée ({len(eligible)} courses), "
            "aucun envoi. Les prochains runs ne notifieront que les nouveautés."
        )
        return 0

    if live and not stored:
        print(
            "\n❌ Pas de token valide. Lance `notify.py token` et accepte la popup "
            "sur le T14 (rien n'a été envoyé ni marqué).",
            file=sys.stderr,
        )
        return 1

    token = stored["access_token"] if stored else None

    # Rappel d'expiration (toujours vers Note to self), tant que le token vit.
    if live and days_left is not None and days_left <= REMINDER_DAYS:
        ok, body = _post_message(
            token, REMINDER_CHAT_ID, _reminder_text(days_left, stored["expires_at"])
        )
        print(
            f"\n🔔 Rappel expiration token ({days_left}j): "
            + ("envoyé" if ok else f"échec {body[:100]}")
        )

    if not new and not joins:
        if live:
            # Snapshot rafraîchi même les jours calmes (ex. désistement),
            # pour qu'une remontée ultérieure du compteur soit bien vue
            # comme une nouvelle inscription.
            _save_notified_ids(known, _snapshot(eligible))
        print("\n✅ Rien de nouveau à notifier.")
        return 0

    if not live:
        for r in new:
            print(f"\n--- {r['id']} ---\n{build_message(r)}")
        n_msgs = 0
        for r, added, joined in joins:
            for tag, msg in _join_messages(r, added, joined):
                n_msgs += 1
                print(f"\n--- {r['id']} ({tag}) ---\n{msg}")
        print(
            f"\n(DRY-RUN) {len(new)} course(s) et {n_msgs} message(s) "
            "d'inscription seraient envoyés. Rien n'a été posté ni marqué. "
            "Ajoute --live pour envoyer."
        )
        return 0

    sent_ids = set()
    failed_ids = set()
    for r in new:
        msg = build_message(r)
        print(f"\n--- {r['id']} ---\n{msg}")
        ok, body = _post_message(token, BEEPER_CHAT_ID, msg)
        if ok:
            print("   ✅ envoyé")
            sent_ids.add(r["id"])
        else:
            print(f"   ❌ échec envoi: {body[:150]}", file=sys.stderr)
            failed_ids.add(r["id"])

    join_sent = set()
    for r, added, joined in joins:
        ok_all = True
        for tag, msg in _join_messages(r, added, joined):
            print(f"\n--- {r['id']} ({tag}) ---\n{msg}")
            ok, body = _post_message(token, BEEPER_CHAT_ID, msg)
            if ok:
                print("   ✅ envoyé")
            else:
                print(f"   ❌ échec envoi: {body[:150]}", file=sys.stderr)
                ok_all = False
        # Course marquée seulement si tous ses messages sont partis, sinon
        # l'ancien snapshot est conservé → retentative complète demain.
        if ok_all:
            join_sent.add(r["id"])
        else:
            failed_ids.add(r["id"])

    # Les courses en échec gardent leur ancien snapshot (retentative demain) ;
    # toutes les autres sont mises à jour.
    new_state = _snapshot([r for r in eligible if r["id"] not in failed_ids])
    for rid in failed_ids:
        if rid in state:
            new_state[rid] = state[rid]
    _save_notified_ids(known | sent_ids | join_sent, new_state)
    total = len(sent_ids) + len(join_sent)
    print(f"\n✅ {total} envoyée(s), {len(failed_ids)} échec(s).")
    return 0


def _join_messages(r: dict, added: int, joined: list[str]) -> list[tuple[str, str]]:
    """Un message par inscription : chaque membre nommé (opt-in) a le sien —
    un 🚫 vise ainsi toujours une seule personne. Le reliquat anonyme
    (non opt-in) est groupé : impossible à individualiser."""
    out: list[tuple[str, str]] = [(n, build_join_message(r, n)) for n in joined]
    anon = added - len(joined)
    if anon > 0:
        out.append((f"{anon} anonyme(s)", build_anon_join_message(r, anon)))
    return out


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
    if cmd == "reactions":
        return cmd_reactions(rest)
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
