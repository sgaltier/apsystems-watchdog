# SPDX-License-Identifier: AGPL-3.0-or-later
FROM python:3.12-slim

# tzdata : indispensable pour que TZ=Europe/Paris soit interprété (heure d'été/hiver)
# ca-certificates : indispensable pour les appels HTTPS vers l'API APsystems
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata ca-certificates \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    TZ=Europe/Paris \
    PV_STATE_FILE=/data/state.json

WORKDIR /app
COPY apsystems_watchdog.py /app/
COPY entrypoint.sh /app/
RUN chmod +x /app/entrypoint.sh && mkdir -p /data

# Le script n'a aucune dépendance externe : pas de pip install.

ENTRYPOINT ["/app/entrypoint.sh"]
