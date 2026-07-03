"""WhatsApp notification loop for newly-detected club races.

The scrape pipeline (scrapers.main) appends newly-detected races to
``data/notifications_queue.json``. This module reads that queue, formats a
WhatsApp message per race, and lets the sender mark races as notified.

The actual *send* is performed by Claude through the Beeper MCP — the CI
pipeline has no Beeper access. The recurring loop is therefore:

    1. CI runs scrapers.main daily  -> queue is filled with new races
    2. `python -m scrapers.cache_cli sync pull`  (bring the queue locally)
    3. `python -m scrapers.notify list`  -> Claude reads the pending messages
    4. Claude posts each message to the "Blabla Run Event 86" group via Beeper
    5. `python -m scrapers.notify mark <id>...`  -> drop them from the queue

The queue only stores opted-in first names + counts (no last names), so it is
safe to inspect or commit.

CLI:
    python -m scrapers.notify list            # formatted messages + ids (JSON)
    python -m scrapers.notify build <id>      # message text for one race
    python -m scrapers.notify mark <id>...    # remove races from the queue
    python -m scrapers.notify clear           # empty the queue
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NOTIFY_QUEUE_PATH = ROOT / "data" / "notifications_queue.json"
NOTIFIED_LOG_PATH = ROOT / "data" / "notified.json"

# "Blabla Run Event 86" WhatsApp group (Beeper chatID).
CLUB_CHAT_ID = "22548"

_MAX_NAMES = 5


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
    if item.get("url"):
        lines += ["", f"➡️ {item['url']}"]
    lines += ["", "Qui d'autre y va ? 👀"]
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
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
