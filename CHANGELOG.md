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
- **Token Beeper** stocké ~30 jours avec rappel avant expiration
  (`notify.py token`), et commande `notify.py test`.
- **`cache_cli repair-archive`** : re-scrape les métadonnées des courses archivées
  sans date ni coordonnées. Les courses passées sortent de la découverte, donc un
  correctif de scraper ne les atteint jamais autrement ; les membres archivés ne
  sont pas touchés. Répare l'archive **locale** ; côté CI, passer par le nouvel
  input `repair_archive` du workflow `scrape.yml` (`races_archive.json` n'a pas de
  chemin d'upload, et `ci clear --all` l'effacerait).
- **Hook de vérification avant push** (`scripts/verify-before-push.sh` +
  `docs-internal/verify-checklist.md`) : force la revue (code, /simplify, doc,
  flows, RGPD) via sous-agents avant chaque `git push`.

### Modifié
- **Notifications groupées en récap mensuel** (`scrapers/notify.py send`,
  demande de Maxime — Team com 2026-09-20) : un seul message par mois,
  envoyé **le dernier jour du mois**, regroupant toutes les courses à venir
  avec membres, au lieu d'un message par nouvelle course et par nouvel
  inscrit. Le mois envoyé est persisté dans `notified.json` (`last_digest`) ;
  échec d'envoi ⇒ mois non marqué. Le récap affiche les **noms complets de
  tous les membres** (groupe privé) : source = artifact GitHub `scraper-data`
  via un PAT fine-grained (`GH_TOKEN`, secret `RUNEVENT86_NOTIFY_GH_TOKEN`),
  repli sur le flux public (prénoms opt-in) s'il est absent.
- **Flux calendrier `.ics` : tout l'historique** (`scrapers/main.py
  generate_ics`) : les courses passées ne sont plus filtrées — l'agenda
  sert aussi d'historique du club (demande Julien 2026-09-26). Les abonnés
  verront les éditions passées apparaître rétroactivement.

### Supprimé
- **Correction des homonymes par réaction 🚫** (`notify.py reactions` + cron
  2h) : incompatible avec les messages groupés. `exclusions.json` reste servi
  par caddy et filtré côté frontend, mais s'édite désormais à la main.

### Corrigé
- **IPITOS : dates, lieux et distances enfin exploitables.** La découverte lisait
  `div.nom`/`div.dt` alors que `live.ipitos.com` expose `div.name`/`div.date` : le
  nom absorbait la date (« Tout Poitiers Courtvendredi 10 avril 2026 ») et les 21
  courses IPITOS arrivaient sans date, dont 13 sans coordonnées — invisibles sur la
  carte et exclues du filtre « À venir ». Le scraper lit désormais l'en-tête
  `<Epreuve>` du `.clax` (nom, ville, date ISO, parcours, type), qui fait autorité.
- **Identités des membres canonicalisées.** Les plateformes renvoyaient la même
  personne sous plusieurs graphies (`ROMAIN RICHARD`, `Romain RICHARD`,
  `RICHARD\xa0Romain`), gonflant le décompte à 85 coureurs distincts pour 36 réels.
  Chaque nom détecté est replié sur son orthographe de `config.yml`.
- **Alerte sur les plateformes muettes.** Un site refondu répond HTTP 200 sans
  correspondre aux sélecteurs : la découverte rend 0 course sans lever d'erreur
  et le run reste vert — c'est ainsi que Protiming est passé inaperçu plusieurs
  jours. La phase 1 récapitule désormais les plateformes qui n'ont rien trouvé,
  et remonte l'avertissement dans le résumé du run GitHub Actions (`::warning::`)
  plutôt que de le noyer dans le log.
- **Protiming : découverte muette depuis la refonte du site.** `protiming.fr` a
  été reconstruit en SPA Tailwind : `/Runnings/liste/…` redirige vers `/events`
  et `div.panel-container` a disparu, donc la découverte trouvait **0 course**
  — sans erreur ni exception, le run restait vert. Réécriture sur les nouvelles
  routes (`/events?page={N}`, `/events/{id}-{slug}/runners`) : **242 courses**
  redécouvertes, et le filtre serveur `?club=` restaure le matching par champ
  club, qui capte les membres absents de `known_members`.
- **Klikego : lieux parasités sur les cartes « Dernière minute ».** La découverte
  prenait le premier `div` dont le texte ressemblait à « Ville, Département (XX) »,
  or `select("div")` renvoie les parents avant les feuilles et le texte concaténé
  d'un parent satisfait le motif tout autant : le lieu devenait « Dernière
  minute20 sept. 2026TRAIL DES GORGES…Montbron, 16 », non géocodable (course
  absente de la carte) ou géocodé de travers (Pons projeté dans le Cantal). Seuls
  les `div` feuilles sont désormais retenus. Le lieu issu de la découverte prime
  aussi sur celui d'une entrée de cache, pour réparer sans attendre l'expiration
  du TTL.
- **Le site ne dépend plus du chargement de la carte.** `loadData()` était
  accroché à `map.on("load")` : sans clé MapTiler valide, le style ne chargeait
  jamais et la page entière restait vide — pas seulement le fond de carte. Les
  données se chargent désormais en parallèle ; seules les couches de la carte
  attendent le style (`whenMapReady()`). Sans clé, repli sur le style libre
  MapLibre pour rester testable en local.
- **Géocodage** : `Laval` (Mayenne, pas les Alpes-Maritimes), `Magné`
  (Deux-Sèvres, pas la Haute-Vienne) et `BaillaRun` (Saint-Georges-lès-Baillargeaux)
  ajoutés aux `OVERRIDES`.
- Déploiement `skip_scrape` : l'étape de build tolère un `races.ics` absent.
