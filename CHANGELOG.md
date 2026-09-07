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
- **`cache_cli repair-archive`** : re-scrape les métadonnées des courses archivées
  sans date ni coordonnées. Les courses passées sortent de la découverte, donc un
  correctif de scraper ne les atteint jamais autrement ; les membres archivés ne
  sont pas touchés. Répare l'archive **locale** ; côté CI, passer par le nouvel
  input `repair_archive` du workflow `scrape.yml` (`races_archive.json` n'a pas de
  chemin d'upload, et `ci clear --all` l'effacerait).
- **Hook de vérification avant push** (`scripts/verify-before-push.sh` +
  `docs-internal/verify-checklist.md`) : force la revue (code, /simplify, doc,
  flows, RGPD) via sous-agents avant chaque `git push`.

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
- Robustesse `reactions` : réconciliation au lieu de recalcul total — les
  exclusions ne sont plus effacées quand le T14 est injoignable, ni quand une
  notif sort de la fenêtre de scan.
- Déploiement `skip_scrape` : l'étape de build tolère un `races.ics` absent.
