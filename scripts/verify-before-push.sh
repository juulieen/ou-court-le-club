#!/bin/bash
# PreToolUse hook (Claude Code) — garde-fou de vérification avant `git push`.
#
# But : sans utiliser `claude -p`, forcer l'agent EN COURS à lancer des
# sous-agents de vérification (revue de code, cohérence flows/features/doc,
# tests) avant que le code ne parte sur le remote. Le hook bloque le `git push`
# (exit 2) et renvoie l'instruction dans le contexte de l'agent, qui doit alors
# lancer les vérifs, puis marquer le SHA vérifié et relancer le push.
#
# Anti-boucle : un sentinel `.claude/.verify-ok` contenant le SHA de HEAD.
#   - marker == HEAD  → push autorisé
#   - sinon           → push bloqué + instruction
# Un nouveau commit (HEAD change) réclame donc une nouvelle vérification.
#
# Bypass ponctuel : préfixer la commande par `SKIP_VERIFY=1`.
set -euo pipefail

input=$(cat)

# Pré-filtre rapide : ni "git" suivi de "push" nulle part → passe (évite de
# lancer python sur toutes les commandes Bash sans rapport). Tolère les espaces
# multiples/tabs pour rester aligné avec le matcher précis plus bas.
printf '%s' "$input" | grep -Eq 'git[[:space:]]+push' || exit 0

# Extrait la commande réelle du JSON du hook (tool_input.command).
cmd=$(printf '%s' "$input" | python3 -c \
  'import sys,json; print((json.load(sys.stdin).get("tool_input") or {}).get("command",""))' \
  2>/dev/null || true)

# Détection précise : `git push` en POSITION DE COMMANDE. On retire d'abord les
# chaînes quotées (pour ne pas matcher `echo "git push"` / `grep 'git push'`),
# puis on exige `git push` en début ou après un séparateur (; & | ( espace).
unquoted=$(printf '%s' "$cmd" | sed "s/'[^']*'//g; s/\"[^\"]*\"//g")
printf '%s' "$unquoted" \
  | grep -Eq '(^|[[:space:];&|(])git[[:space:]]+push([[:space:]]|$)' || exit 0

# Bypass explicite.
case "$cmd" in
  *SKIP_VERIFY=1*) exit 0 ;;
esac

root="${CLAUDE_PROJECT_DIR:-$(pwd)}"
cd "$root" 2>/dev/null || exit 0
head=$(git rev-parse HEAD 2>/dev/null || true)
[ -n "$head" ] || exit 0  # pas un dépôt git → on ne bloque pas

marker="$root/.claude/.verify-ok"
if [ -f "$marker" ] && [ "$(cat "$marker" 2>/dev/null || true)" = "$head" ]; then
  exit 0  # déjà vérifié pour ce HEAD
fi

# Bloque et délègue à l'agent dédié (contexte principal gardé propre).
cat >&2 <<EOF
🛑 Vérification requise avant de pousser (HEAD ${head:0:12}).

Lance l'agent de vérification dédié (il porte la checklist projet dans SON
propre contexte — checklist : docs-internal/verify-checklist.md) :

  Agent(subagent_type="pre-push-verifier",
        prompt="Vérifie le diff à pousser (origin/main..HEAD).")

Selon son verdict :
  • VERDICT: PASS → si un test navigateur est signalé, lance-le (sous-agent
    chrome-devtools) et attends qu'il soit vert ; puis marque le SHA vérifié
    DANS UNE COMMANDE SÉPARÉE et relance le push :
        git rev-parse HEAD > .claude/.verify-ok
        git push ...
  • VERDICT: FAIL → corrige les points remontés, re-commit, relance l'agent.

Bypass ponctuel (cas trivial / vérif déjà faite) : préfixe par SKIP_VERIFY=1.
EOF
exit 2
