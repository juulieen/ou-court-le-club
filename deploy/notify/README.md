# Notifieur de courses — conteneur

Poste un message Beeper/WhatsApp à chaque **nouvelle course à venir** détectée
pour le club, une fois par jour, après le scan CI.

## Comment ça marche

1. La CI GitHub (cloud) scanne à 06:00 UTC et déploie `races.json` sur GitHub Pages.
2. Ce conteneur (sur l'ASUS) réveille un cron à **07:00 UTC** (`crontab`).
3. `notify.py send` récupère le `races.json` public, le compare à `notified.json`
   (persistant dans le volume `/data`), et poste les nouveautés.
4. L'envoi passe par l'**API Beeper Desktop du T14** (`***REMOVED***:23373`),
   joignable via Tailscale, avec un **token OAuth valable ~30 jours**.

Le cloud CI ne peut pas joindre le T14 (pas de Tailscale) : c'est pourquoi
l'envoi vit dans ce conteneur côté LAN, et non dans le workflow.

## Sécurité / garde-fous

- **Dry-run par défaut** (`NOTIFY_ARGS=""`) : le conteneur logue ce qu'il
  enverrait sans rien poster. Passer à `--live` pour activer l'envoi réel.
- **Cible par défaut = "Note to self"** (`***REMOVED***`).
  Groupe club seulement après validation :
  `***REMOVED***` (« Blabla Run Event 86 », WhatsApp).
- **Premier run = baseline** : mémorise les courses déjà visibles sans rien
  envoyer, pour ne pas spammer tout l'historique. Les runs suivants ne
  notifient que le nouveau.
- Le T14 doit être allumé au moment du run (sinon échec silencieux, réessai le
  lendemain — notifs non urgentes).

## Déploiement (sur l'ASUS)

```bash
# copier deploy/notify/ sur l'ASUS puis :
cd deploy/notify
docker compose -f compose.yaml up -d --build
docker logs runevent86-notify          # voir les runs du cron

# tester tout de suite un run à la main (dry-run) :
docker exec runevent86-notify /app/run.sh
# passer en envoi réel : mettre NOTIFY_ARGS="--live" dans compose.yaml puis
docker compose -f compose.yaml up -d
```

## Variables (compose.yaml)

| Var | Défaut | Rôle |
|---|---|---|
| `RACES_URL` | Pages public | source du races.json |
| `BEEPER_API` | `http://***REMOVED***:23373` | API Desktop T14 (Tailscale) |
| `BEEPER_CHAT_ID` | Note to self | conversation cible |
| `NOTIFY_ARGS` | `""` (dry-run) | `--live` pour envoyer |
| `NOTIFIED_PATH` | `/data/notified.json` | log de dédup (volume) |

## Correction des homonymes (réaction 🚫)

Le matching par nom attrape parfois un homonyme (même prénom, pas un membre).
Sur une notif, **réagir avec 🚫 masque la course de la carte** — n'importe quel
membre peut le faire, aucune identité n'est stockée : l'id de course est lu dans
le lien `#race/<id>` du message.

- `notify.py reactions` (cron toutes les 2h) relit les réactions et réécrit
  `exclusions.json` (recalcul complet → **enlever la réaction ré-affiche** la
  course). Écrit dans `./public/` (volume partagé avec caddy).
- caddy sert `./public/exclusions.json` sur `https://run.juulieen.fr/exclusions.json`
  (bloc dédié dans le Caddyfile de l'ASUS, en-tête CORS `*`).
- Le frontend (`app.ts` → `EXCLUSIONS_URL`) le charge et filtre les courses ;
  best-effort (si l'URL est down, on affiche tout).

**⚠️ Pré-requis DNS** : un enregistrement **A `run.juulieen.fr` → ***REMOVED*****
(comme les autres sous-domaines). Sans lui, caddy ne peut pas émettre le certif
et le frontend retombe simplement sur « aucune exclusion ».

## Token Beeper (~30 jours)

Obtenir un token demande d'**accepter une popup sur le T14** — donc on le prend
une fois et on le réutilise :

```bash
docker exec -it runevent86-notify python /app/notify.py token
# → accepte la popup sur Beeper (T14). Token stocké dans /data (volume), ~30j.
```

Le `send` ne redemande **jamais** de token automatiquement (sinon il faudrait
accepter la popup à chaque run). À la place, quand le token approche de
l'expiration (`TOKEN_REMINDER_DAYS`, 3j par défaut), il poste un **rappel dans
"Note to self"** avec la commande à relancer. Il suffit de refaire un
`notify.py token` à ce moment-là.
