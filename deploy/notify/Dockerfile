# RunEvent86 — notifieur de nouvelles courses (cron dans un conteneur).
# Récupère le races.json public, diffe vs notified.json, poste les nouveautés
# vers l'API Beeper Desktop (T14) via Tailscale. Aucune dépendance pip : le
# `send` de notify.py n'utilise que la stdlib.
FROM python:3.12-slim

# tzdata : nécessaire pour que supercronic résolve TZ=Europe/Paris (sinon le
# cron retombe sur UTC). slim ne l'embarque pas.
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

# supercronic : un cron pensé pour les conteneurs (logue les jobs sur stdout,
# donc visible dans `docker logs`).
ARG SUPERCRONIC_VERSION=v0.2.33
ARG SUPERCRONIC_SHA1SUM=71b0d58cc53f6bd72cf2f293e09e294b79c666d8
ADD https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-amd64 /usr/local/bin/supercronic
RUN echo "${SUPERCRONIC_SHA1SUM}  /usr/local/bin/supercronic" | sha1sum -c - \
    && chmod +x /usr/local/bin/supercronic

WORKDIR /app
COPY notify.py /app/notify.py
COPY run.sh /app/run.sh
COPY crontab /app/crontab
RUN chmod +x /app/run.sh

# Persistance du log de dédup dans un volume monté sur /data.
ENV NOTIFIED_PATH=/data/notified.json
VOLUME ["/data"]

# Chemin ABSOLU obligatoire : en PID 1, supercronic se ré-exécute via
# execve(os.Args[0]) pour son reaper, et execve ne cherche pas dans le PATH.
CMD ["/usr/local/bin/supercronic", "/app/crontab"]
