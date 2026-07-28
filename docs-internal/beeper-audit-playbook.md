# Workflow — Audit Beeper → Scrapers

Objectif : vérifier que les courses où des membres **Run Event 86** s'inscrivent
(mentionnées dans les groupes WhatsApp via Beeper) sont bien captées par les
scrapers et apparaissent sur la carte. Repérer les trous et proposer des fixes.

> Ce workflow est piloté par Claude (l'accès Beeper passe par le MCP, pas par une
> API que le pipeline Python pourrait appeler seul). Le rejouer = relancer Claude
> avec ce playbook.

## Étape 1 — Lire les messages Beeper du club

Comptes : `whatsapp`. Groupes pertinents :

| Chat | chatID | Rôle |
|---|---|---|
| Run Event 86 | 22097 | Annonces officielles |
| Blabla Run Event 86 | 22548 | Bla-bla, résultats de courses ← principale source |
| Mardi ⛰️ trail nature/urbain | 22327 | Sorties trail, plans de courses |
| Jeudi 🏟️ séances piste | 22028 | Séances, mentions de courses |
| Orga Ekiden | 22569 | Organisation Ekiden |
| Orga TTT 2026 | 22539 | Organisation Tic Tac Trail |

Outils : `mcp__beeper__list_messages` (fil récent) et `mcp__beeper__search_messages`
(recherche littérale, **mots simples**, `accountIDs:["whatsapp"]`, pas de `chatIDs`
numériques → bug "Invalid input", filtrer ensuite par nom de chat).

Mots-clés utiles : `trail`, `course`, `inscription`, `dossard`, `km`, `marathon`,
`semi`, `ekiden`, `bonne course`, `résultat`. Repérer aussi les liens partagés
(`onsinscrit.com`, `njuko`, `klikego`, `helloasso`, `sportsnconnect`, etc.).

Produire une liste : `nom course | date | membre(s) cité(s) | lien éventuel`.

## Étape 2 — Identifier la plateforme de chaque course (Chrome, via subagent)

Pour chaque course **non encore présente dans `data/races.json`**, lancer un
subagent avec les outils `mcp__chrome-devtools__*` (cf. consigne globale : Chrome
toujours via subagent). Pour chacune, déterminer :

- la **plateforme** (klikego, njuko, onsinscrit, protiming, chronometrage,
  chrono-start, 3wsport, espace-competition, sportips, timepulse, endurance-chrono,
  listino, ipitos, sportsnconnect, helloasso, ou **AUTRE/inconnue**) ;
- l'**URL de la liste des inscrits** ;
- si un **membre** y figure (champ club = variante "Run Event 86", ou nom connu) ;
- si le **champ club** est public.

Le subagent reçoit la logique de matching (patterns + known_members de `config.yml`).

## Étape 3 — Croiser avec la couverture scrapers

```bash
python3 -c "import json; d=json.load(open('data/races.json')); \
[print(r['date'], r['platform'], r['name']) for r in sorted(d['races'], key=lambda x:x.get('date',''))]"
```

Classer chaque course Beeper :

- **✅ Captée** — présente dans `races.json`.
- **⏳ Capté­able mais pas encore run** — plateforme supportée, mais `last_updated`
  de `races.json` antérieur à l'ouverture des inscriptions → relancer le pipeline.
- **🔧 Trou plateforme** — plateforme non supportée → nouveau scraper.
- **👻 Membre invisible** — plateforme OK mais membre absent du champ club ET de
  `known_members` → ajouter à `known_members`.
- **🚫 Non scrappable** — HelloAsso & co (inscrits privés) → entrée manuelle
  `config.yml`.

## Étape 4 — Proposer / appliquer les fixes

- **Ajout `known_members`** (`config.yml`) — sûr, détection seule, aucun impact RGPD
  (l'affichage reste gouverné par `display_optin`). Format `"NOM Prenom"`.
- **Ajout `display_optin`** — ⚠️ nécessite le **consentement** explicite du membre.
  Ne jamais ajouter sans validation.
- **Entrée manuelle** (HelloAsso, événements du club) sous `races:` avec
  `platform: manual`, `members:` (uniquement membres consentants pour l'affichage).
- **Nouveau scraper** — suivre "How to Add a New Scraper" du `CLAUDE.md`.
- **Déploiement** après modif `config.yml` :
  `gh secret set CONFIG_YML < config.yml` puis `gh workflow run scrape.yml`.

## Archivage des courses passées

`scrapers.main` maintient un **archive persistant** `data/races_archive.json` :
chaque course déjà trouvée avec des membres y est conservée même quand sa liste
d'inscrits disparaît après l'événement. À chaque run :

- les courses re-trouvées **écrasent** leur version archivée (données fraîches) ;
- les courses passées non re-découvertes sont **conservées** (membres + lat/lng) ;
- la sortie (`races.json` privé + `docs/data/races.json` public) = **union**
  archive + courses du run → alimente les filtres frontend « Récentes » / « Toutes ».

Persistance CI : `data/races_archive.json` est ajouté aux 3 listes de chemins
`actions/cache` de `.github/workflows/scrape.yml` (Restore / Save / Upload).

## Boucle de notification WhatsApp (prévue — pas encore dans le repo)

But : prévenir le groupe « Blabla Run Event 86 » dès qu'une **nouvelle** course
avec ≥1 membre est détectée → fait remonter les membres manquants
(« moi aussi j'y suis »).

`merge_archive` renvoie déjà la liste des courses jamais vues (`new_races`), ce
qui fournit le déclencheur. Reste à brancher l'envoi. Architecture retenue
(**Option 1**, à implémenter quand le serveur de prod sera up — il a un Beeper
Server installé, hôte 24/7 idéal) :

- Beeper expose une API HTTP locale :
  `POST http://localhost:23373/v1/chats/{chatID}/messages` (auth Bearer, token via
  Réglages → Integrations). Chat cible : voir le secret GitHub `RUNEVENT86_NOTIFY_CHAT_ID_PROD`.
- Un cron/systemd sur l'hôte Beeper lance un sender (`notify.py --send`, à
  committer) : fetch la `races.json` publique → diff vs `data/notified.json` →
  POST chaque nouvelle course → marque l'id notifié. **Aucun Claude dans la boucle.**
- Contraintes : l'API WhatsApp officielle ne poste pas en groupe, et la CI cloud
  ne joint pas le `localhost` → le sender doit tourner où Beeper tourne.
  Alternative robuste 24/7 : miroir Telegram (Bot API depuis la CI).

> ⚠️ Ne jamais auto-poster dans le groupe (~20 personnes) sans validation.
> Tester en dry-run d'abord.

## Historique des audits

### 2026-06-21 (premier passage)

| Course | Date | Plateforme | Statut | Action |
|---|---|---|---|---|
| Urban Trail de Poitiers | 12/09 | sportsnconnect | ⏳ captable, pas encore run | relancer pipeline |
| Trail Abbaye de Valence | 21/06 | onsinscrit | ⏳ (course passée) | RAS, valider au prochain run |
| Ekiden de Poitiers | 28/06 | helloasso | 🚫 non scrappable | entrée manuelle à compléter |
| Cascade d'Ars (Julien) | 14/06 | chrono-start | ✅ captée | — |
| Fée Mélusine, Pastourelle, Cardineau | — | onsinscrit/njuko | ✅ captées | — |

Nouveaux membres détectés (champ club rempli) → ajoutés à `known_members` :
MOINE Adeline, RAVELEAU Thomas, GUENERON Marie, BARBIER Florent.

Constat clé : `data/races.json` local figé au 31/03 → **le pipeline n'a pas tourné
depuis 3 mois en local**. La plupart des trous se résolvent par un simple re-run.
</content>
</invoke>
