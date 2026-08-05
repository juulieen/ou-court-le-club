# Notifieur de courses — conteneur

Poste un message Beeper/WhatsApp à chaque **nouvelle course à venir** détectée
pour le club, et **un message par nouvelle inscription** sur une course déjà
connue (compteur de membres en hausse ; un message par membre opt-in nommé —
pour qu'un 🚫 vise toujours une seule personne — plus un message groupé pour
l'éventuel reliquat anonyme), une fois par jour, après le scan CI.

## Comment ça marche

1. La CI GitHub (cloud) scanne à 06:00 UTC et déploie `races.json` sur GitHub Pages.
2. Ce conteneur (sur le serveur perso) réveille un cron à **11h30 Europe/Paris**
   (`crontab`, `TZ` du conteneur) ; les réactions 🚫 sont relues **toutes les 2h**.
3. `notify.py send` récupère le `races.json` public, le compare à `notified.json`
   (persistant dans le volume `/data`), et poste les nouveautés.
4. L'envoi passe par l'**API Beeper Desktop** d'une machine du réseau Tailscale
   (voir `BEEPER_API` dans `.env`), avec un **token OAuth valable ~30 jours**.

Le cloud CI ne peut pas joindre cette machine (pas de Tailscale) : c'est pourquoi
l'envoi vit dans ce conteneur côté LAN, et non dans le workflow.

## Sécurité / garde-fous

- **Dry-run par défaut** (`NOTIFY_ARGS=""`) : le conteneur logue ce qu'il
  enverrait sans rien poster. Passer à `--live` pour activer l'envoi réel.
- **Cible par défaut = "Note to self"**. Groupe club seulement après validation
  (ID conservé dans le secret GitHub `RUNEVENT86_NOTIFY_CHAT_ID_PROD`).
- **Premier run = baseline** : mémorise les courses déjà visibles sans rien
  envoyer, pour ne pas spammer tout l'historique. Les runs suivants ne
  notifient que le nouveau.
- La machine Beeper doit être allumée au moment du run (sinon échec silencieux,
  réessai le lendemain — notifs non urgentes).

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
| `NOTIFY_ARGS` | `""` (dry-run) ou `--live` |

Fixes dans `compose.yaml` : `RACES_URL` (Pages public), `NOTIFIED_PATH` et
`TOKEN_PATH` (dans le volume `/data`), `TOKEN_REMINDER_DAYS` (3j).

## Correction des homonymes (réaction 🚫)

Le matching par nom attrape parfois un homonyme (même prénom, pas un membre).
Sur une notif, **réagir avec 🚫 masque le membre cité par le message** — pas la
course entière : les autres inscrits restent sur la carte. N'importe quel
membre peut le faire, aucune identité de réacteur n'est stockée : l'id de
course (lien `#race/<id>`) et le prénom affiché (ligne 🎉/👥) sont lus dans le
message lui-même.

- Chaque nouvelle inscription donne lieu à **un message par membre nommé** :
  un 🚫 vise donc toujours une seule personne. Cas ambigus (annonce de course
  à plusieurs noms, inscrit non opt-in « prénom non public ») : rien n'est
  masqué, le bot poste **une** demande de précision → exclusion à la main dans
  `exclusions.json` (entrée `{"race": "<id>", "name": "<prénom affiché>"}`).
- `notify.py reactions` (cron toutes les 2h) relit les réactions et réécrit
  `exclusions.json` (format `{members: [{race, name}], asked: [...]}` →
  **enlever la réaction ré-affiche** le membre). Écrit dans `./public/`
  (volume partagé avec caddy).
- Le frontend retire le prénom de `first_names` et décrémente `member_count` ;
  la course ne disparaît que si le compteur tombe à 0 (mono-inscrit).
- caddy sert `./public/exclusions.json` sur `https://run.juulieen.fr/exclusions.json`
  (bloc dédié dans le Caddyfile du serveur, en-tête CORS `*`).
- Le frontend (`app.ts` → `EXCLUSIONS_URL`) le charge et filtre les courses ;
  best-effort (si l'URL est down, on affiche tout).

**⚠️ Pré-requis DNS** : un enregistrement **A `run.juulieen.fr`** pointant vers
le serveur (comme les autres sous-domaines). Sans lui, caddy ne peut pas
émettre le certif et le frontend retombe simplement sur « aucune exclusion ».

## Token Beeper (~30 jours)

Obtenir un token demande d'**accepter une popup sur Beeper Desktop** — donc on
le prend une fois et on le réutilise :

```bash
docker exec -it runevent86-notify python /app/notify.py token
# → accepte la popup sur Beeper Desktop. Token stocké dans /data (volume), ~30j.
```

Le `send` ne redemande **jamais** de token automatiquement (sinon il faudrait
accepter la popup à chaque run). À la place, quand le token approche de
l'expiration (`TOKEN_REMINDER_DAYS`, 3j par défaut), il poste un **rappel dans
"Note to self"** avec la commande à relancer. Il suffit de refaire un
`notify.py token` à ce moment-là.
