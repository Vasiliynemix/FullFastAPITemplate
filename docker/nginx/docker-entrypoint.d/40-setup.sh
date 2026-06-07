#!/bin/sh
# ============================================================
# Подготовка nginx перед стартом:
# 1) подставить SERVER_NAME в конфиг (envsubst);
# 2) если нет сертификата для домена — сгенерировать self-signed заглушку,
#    чтобы nginx стартовал ДО выпуска реального cert через certbot.
# ============================================================
set -e

: "${SERVER_NAME:=localhost}"
export SERVER_NAME

# 1) Подстановка переменной в конфиг (сохраняем $nginx-переменные нетронутыми)
envsubst '${SERVER_NAME}' < /etc/nginx/conf.d/default.conf > /etc/nginx/conf.d/default.conf.tmp
mv /etc/nginx/conf.d/default.conf.tmp /etc/nginx/conf.d/default.conf

# 2) Self-signed fallback, если Let's Encrypt-сертификата ещё нет
CERT_DIR="/etc/letsencrypt/live/${SERVER_NAME}"
if [ ! -f "${CERT_DIR}/fullchain.pem" ]; then
    echo "[nginx-setup] no cert for ${SERVER_NAME}, generating self-signed fallback"
    mkdir -p "${CERT_DIR}"
    openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
        -keyout "${CERT_DIR}/privkey.pem" \
        -out "${CERT_DIR}/fullchain.pem" \
        -subj "/CN=${SERVER_NAME}" 2>/dev/null
fi
