#!/usr/bin/env bash
# ============================================================
# Первичный выпуск SSL-сертификата Let's Encrypt (Dockerized certbot).
#
# Запуск (с уже поднятым nginx):
#   SERVER_NAME=example.com CERTBOT_EMAIL=you@example.com \
#   bash docker/certbot/init-letsencrypt.sh
#
# Использует webroot-режим: nginx отдаёт /.well-known/acme-challenge/ из тома.
# После выпуска nginx нужно перезагрузить (compose делает это автоматически в loop).
# ============================================================
set -euo pipefail

SERVER_NAME="${SERVER_NAME:?set SERVER_NAME=your.domain}"
CERTBOT_EMAIL="${CERTBOT_EMAIL:?set CERTBOT_EMAIL=you@domain}"
STAGING="${STAGING:-0}"   # 1 = тестовый сервер LE (без лимитов), 0 = боевой

staging_arg=""
if [ "$STAGING" != "0" ]; then
    staging_arg="--staging"
fi

echo "[certbot] requesting certificate for ${SERVER_NAME} (staging=${STAGING})"

docker compose run --rm --entrypoint "\
  certbot certbot certonly --webroot -w /var/www/certbot \
    ${staging_arg} \
    --email ${CERTBOT_EMAIL} \
    -d ${SERVER_NAME} \
    --rsa-key-size 4096 \
    --agree-tos \
    --no-eff-email \
    --force-renewal"

echo "[certbot] reloading nginx to pick up the new certificate"
docker compose exec nginx nginx -s reload
echo "[certbot] done"
