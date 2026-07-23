# Changelog

Historique des évolutions notables. Format inspiré de
[Keep a Changelog](https://keepachangelog.com/fr/). Dates en `AAAA-MM-JJ`.

## [Non publié]

### Ajouté
- **Notifications de courses** (`scrapers/notify.py` commande `send`) : détecte
  les nouvelles courses à venir avec des membres et poste un message vers
  l'API Beeper Desktop du T14 (via Tailscale). Lien vers « Où court le club »
  (deep-link `#race/<id>`). Dry-run par défaut, cible « Note to self ».
- **Conteneur notifieur** (`deploy/notify/`) : image Docker + cron supercronic
  (11h30 Europe/Paris) déployée sur l'ASUS.
- **Correction des homonymes** : réagir 🚫 sur une notif masque la course de la
  carte (`notify.py reactions` → `exclusions.json` servi par caddy sur
  `run.juulieen.fr`, filtré côté frontend). Réversible, sans stockage d'identité.
- **Token Beeper** stocké ~30 jours avec rappel avant expiration
  (`notify.py token`), et commande `notify.py test`.
- **Hook de vérification avant push** (`scripts/verify-before-push.sh` +
  `docs-internal/verify-checklist.md`) : force la revue (code, /simplify, doc,
  flows, RGPD) via sous-agents avant chaque `git push`.

### Corrigé
- Robustesse `reactions` : réconciliation au lieu de recalcul total — les
  exclusions ne sont plus effacées quand le T14 est injoignable, ni quand une
  notif sort de la fenêtre de scan.
- Déploiement `skip_scrape` : l'étape de build tolère un `races.ics` absent.
