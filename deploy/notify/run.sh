#!/bin/sh
# Lancé par supercronic. NOTIFY_ARGS="" => dry-run (défaut, sûr) ;
# NOTIFY_ARGS="--live" => envoi réel. Le reste de la config est en env
# (RACES_URL, BEEPER_API, BEEPER_CHAT_ID, NOTIFIED_PATH).
set -eu
echo "[notify] $(date -u +%FT%TZ) run (args='${NOTIFY_ARGS:-}')"
exec python /app/notify.py send ${NOTIFY_ARGS:-}
