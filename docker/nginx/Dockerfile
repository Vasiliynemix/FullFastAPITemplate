FROM nginx:1.27-alpine

# openssl — для self-signed fallback; envsubst уже есть в образе nginx
RUN apk add --no-cache openssl

# Удаляем дефолтный конфиг, кладём свой production-grade
RUN rm -f /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/nginx.conf
COPY conf.d/ /etc/nginx/conf.d/
# Скрипты в /docker-entrypoint.d/ выполняются официальным entrypoint'ом nginx
COPY docker-entrypoint.d/40-setup.sh /docker-entrypoint.d/40-setup.sh
RUN chmod +x /docker-entrypoint.d/40-setup.sh

EXPOSE 80 443
