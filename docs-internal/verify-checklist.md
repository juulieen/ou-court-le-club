# Checklist de vérification avant `git push`

Appliquée par le hook Claude Code `scripts/verify-before-push.sh`. Sur un
`git push`, l'agent Claude en cours **doit** exécuter les vérifs pertinentes au
diff (via les skills/agents indiqués), corriger ce qui échoue, puis marquer le
SHA vérifié et relancer :

```bash
git rev-parse HEAD > .claude/.verify-ok
git push ...
```

**Tous les points sont bloquants.** Bypass ponctuel (cas trivial / vérif déjà
faite) : préfixer par `SKIP_VERIFY=1`.

Chaque item indique **l'outil** à utiliser — privilégier les skills/agents
Claude Code dédiés plutôt qu'un prompt ad-hoc. Ne faire que les points
pertinents au diff.

---

## 1. RGPD / secrets — toujours · `/security-review` + grep
- `/security-review` sur les changements en cours.
- Vérif ciblée : aucun **nom de famille** ni donnée perso dans la sortie
  publique (`docs/data/races.json`) — seuls les prénoms `display_optin` ;
  **aucun secret committé** (token Beeper, `config.yml`, clés, `.env`).

## 2. Revue de code — si du code change · `/code-review`
- `/code-review` sur le diff. Aucun point bloquant non résolu.

## 3. Simplification — si du code change · `/simplify`
- `/simplify` (réutilisation, simplification, efficacité, altitude). Appliquer
  les nettoyages retenus ; noter ceux écartés.

## 4. Build / compile — si du code change · commandes (déterministe)
- Frontend touché → `cd frontend && npm run build`.
- Python touché → `python -m py_compile scrapers/*.py`.

## 5. Flow réel — selon la zone touchée · sous-agent navigateur / scraper
- **Frontend** modifié → sous-agent navigateur (chrome-devtools) : la carte
  charge, pas d'erreur console bloquante, le flow modifié fonctionne.
- **Scraper** modifié → une course de test est bien découverte / trouvée.

## 6. Doc à jour — bloquant · `claude-md-management` + CHANGELOG manuel
- `CLAUDE.md` reflète l'état courant (skill `claude-md-management` /
  `claude-md-improver` pour auditer) : nouveau scraper, nouvelle commande,
  archi, tableau des plateformes, limitations.
- `CHANGELOG.md` : une entrée ajoutée pour la feature / le fix.

## 7. Cohérence déploiement — si `deploy/` concerné · commande (déterministe)
- Fichiers `deploy/` en phase avec le code, ex. :
  `diff scrapers/notify.py deploy/notify/notify.py` (doit être vide).

## 8. Garde-fous préservés — toujours · inclus dans la revue de code
- Dry-run par défaut, cible « Note to self », pas d'auto-post au groupe sans
  action explicite.
