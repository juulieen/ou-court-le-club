# Notifieur de courses — conteneur

Poste **une fois par mois un unique message court** Beeper/WhatsApp, le
**dernier jour du mois** : une ligne par course du mois qui vient (date, nom,
lieu, membres), les courses plus lointaines réduites à un compte, un seul lien
vers la carte (demande de Maxime, Team com 2026-09-20 — les notifs au fil de l'eau
noyaient la conversation). Le récap affiche **les noms complets de tous les
membres** (le message reste dans le groupe privé) : ils proviennent du
`races.json` complet, téléchargé depuis l'artifact GitHub `scraper-data` du
workflow scrape (repo privé, PAT fine-grained `Actions: read` dans
`GH_TOKEN`). Sans `GH_TOKEN`, repli sur le `races.json` public (prénoms
opt-in uniquement).

## Comment ça marche

1. La CI GitHub (cloud) scanne à 06:00 UTC et déploie `races.json` sur GitHub Pages.
2. Ce conteneur (sur le serveur perso) réveille un cron à **11h30, 18h et 21h Europe/Paris**
   (`crontab`, `TZ` du conteneur).
3. `notify.py send` récupère les courses (artifact GitHub si `GH_TOKEN`,
   sinon flux public) et, **le dernier jour du mois** si le récap n'a pas
   encore été envoyé (`notified.json` persistant dans le volume `/data`,
   `{"last_period": "YYYY-MM"}` = dernier mois annoncé), poste UN message
   groupé puis marque le mois. En cas d'échec, nouvel essai chaque jour
   jusqu'au 7 du mois annoncé (`DIGEST_CATCHUP_DAYS`).
4. L'envoi passe par l'**API Beeper Desktop** d'une machine du réseau Tailscale
   (voir `BEEPER_API` dans `.env`), avec un **token OAuth valable ~30 jours**.

Le cloud CI ne peut pas joindre cette machine (pas de Tailscale) : c'est pourquoi
l'envoi vit dans ce conteneur côté LAN, et non dans le workflow.

## Sécurité / garde-fous

- **Dry-run par défaut** (`NOTIFY_ARGS=""`) : le conteneur logue ce qu'il
  enverrait sans rien poster. Passer à `--live` pour activer l'envoi réel.
- **Cible par défaut = "Note to self"**. Groupe club seulement après validation
  (ID conservé dans le secret GitHub `RUNEVENT86_NOTIFY_CHAT_ID_PROD`).
- **Un seul envoi par mois** : le mois annoncé est marqué dans
  `notified.json` après un envoi réussi ; les autres runs ne font rien. Si
  l'envoi du dernier jour échoue (T14 éteint, token expiré…), le mois n'est pas
  marqué et le run quotidien retente jusqu'au 7 du mois annoncé. (Avant le
  2026-10-17, un échec le dernier jour faisait sauter le mois : c'est ce qui
  est arrivé au récap de fin septembre, token expiré le 23/09.)
- **Alertes hors Beeper** (`ALERT_URL`, ex. topic ntfy) : à chaque run tant
  que le token est expiré, et à chaque échec d'un récap dû. Beeper ne peut pas
  prévenir de sa propre panne.

## Déploiement

**Automatique (CI)** : le workflow `.github/workflows/deploy-notify.yml`
déploie sur le serveur perso via un runner self-hosted à chaque push sur
`main` touchant `deploy/notify/**` ou `scrapers/notify.py`. Il génère le
`.env` depuis les secrets GitHub (`RUNEVENT86_NOTIFY_*`), rsync vers le
chemin de déploiement, puis rebuild le conteneur.

**Manuel** :

```bash
cp .env.example .env   # remplir les valeurs
# copier deploy/notify/ sur le serveur puis :
cd deploy/notify
docker compose -f compose.yaml up -d --build
docker logs runevent86-notify          # voir les runs du cron

# tester tout de suite un run à la main (dry-run) :
docker exec runevent86-notify /app/run.sh
# passer en envoi réel : NOTIFY_ARGS=--live dans .env puis
docker compose -f compose.yaml up -d
```

## Variables (.env)

| Var | Rôle |
|---|---|
| `BEEPER_API` | URL de l'API Beeper Desktop (Tailscale) |
| `BEEPER_CHAT_ID` | conversation cible des notifs |
| `BEEPER_REMINDER_CHAT_ID` | conversation pour les rappels d'expiration |
| `GH_TOKEN` | PAT fine-grained (`Actions: read`) — noms complets via l'artifact `scraper-data` (vide = prénoms opt-in) |
| `ALERT_URL` | alerte hors Beeper (ex. `https://ntfy.sh/<topic>`) — vide = logs seulement |
| `NOTIFY_ARGS` | `""` (dry-run) ou `--live` |

Fixes dans `compose.yaml` : `RACES_URL` (Pages public), `NOTIFIED_PATH` et
`TOKEN_PATH` (dans le volume `/data`), `TOKEN_REMINDER_DAYS` (7j).

## Correction des homonymes (vue club, un clic)

Le matching par nom attrape parfois un homonyme. Dans la vue club
(`courses.juulieen.fr`, tailnet only), les inscrits trouvés par le nom seul
sont marqués « (?) » et la fiche de la course propose **« ✕ pas un membre »**
(et « rétablir » pour annuler).

- Le bouton appelle `POST https://courses.juulieen.fr/api/exclusions`, routé
  par caddy vers le service `runevent86-exclusions` (`notify.py serve`,
  publié sur `127.0.0.1:8786` seulement). Corps `{race, name, exclude}` en
  `text/plain` (pas de preflight CORS) ; l'en-tête `Origin` doit être le site
  public ou le dev local, et le couple (course, nom) doit exister dans
  `private/races.json`.
- **Source de vérité : `./private/exclusions.json`** (noms complets, servi sur
  le tailnet seulement). Le service en dérive **`./public/exclusions.json`**
  (servi sur `https://run.juulieen.fr/exclusions.json`) avec le **nom public**
  du membre (prénom opt-in, via `public_names` de `races_private.json`) ou
  `null` pour un inscrit anonyme — **jamais un nom complet**.
- Le frontend retire le nom et décrémente `member_count` (`name: null` →
  décompte seul) ; une course tombée à 0 disparaît de la carte, mais sa fiche
  reste ouvrable dans la vue club pour la rétablir.
- Le récap mensuel applique aussi les exclusions privées.

**Caddy** (bloc `courses.juulieen.fr` du Caddyfile de `asus-1`) :

```
handle /api/* {
	reverse_proxy 127.0.0.1:8786
}
```

## Vue privée (noms complets) — courses.juulieen.fr

Une version des données avec les **noms complets de tous les membres actuels**
est servie par un vhost caddy **bindé tailnet-only** (`bind 100.117.235.23`)
sur `courses.juulieen.fr` — invisible depuis Internet (le catch-all y répond
404), aucune auth : la frontière réseau fait office de protection.

- Le pipeline écrit `data/races_private.json` (même schéma que le public,
  mais `first_names` = noms complets), inclus dans l'artifact `scraper-data`.
- Le job `deploy-private` de `scrape.yml` (runner self-hosted ASUS) dépose le
  fichier dans `./private/races.json` à chaque run — **les deux sites sont
  mis à jour par la même CI**. Le cron `notify.py fetch-private` (11h35) de
  ce conteneur reste en filet de sécurité.
- Caddy sert `./private` (monté `/srv/runevent86-private`) avec
  `Access-Control-Allow-Origin *` ; le frontend public tente ce fetch au
  chargement — succès = appareil sur le tailnet → noms complets partout ;
  échec → affichage public normal, sans erreur visible.
- Rewrite AdGuard `courses.juulieen.fr → 100.117.235.23` pour les appareils
  du LAN (même pattern que `s.`/`k1.`) ; hors LAN, Tailscale doit être actif
  sur l'appareil.

## Token Beeper (~30 jours)

Obtenir un token demande d'**accepter une popup sur Beeper Desktop** — donc on
le prend une fois et on le réutilise :

```bash
docker exec -it runevent86-notify python /app/notify.py token
# → accepte la popup sur Beeper Desktop. Token stocké dans /data (volume), ~30j.
```

Le `send` ne redemande **jamais** de token automatiquement (sinon il faudrait
accepter la popup à chaque run). À la place, quand le token approche de
l'expiration (`TOKEN_REMINDER_DAYS`, 7j par défaut), il poste **chaque jour**
un **rappel dans "Note to self"** avec la commande à relancer ; une fois le
token expiré, l'alerte passe par `ALERT_URL`. Il suffit de refaire un
`notify.py token` à ce moment-là.
