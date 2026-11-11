# Image de sauvegarde : pg_dump (même version majeure que le serveur PostgreSQL),
# mongodump/mongorestore et cron (busybox crond).
ARG POSTGRES_IMAGE=postgres:16-alpine
FROM ${POSTGRES_IMAGE}

RUN apk add --no-cache mongodb-tools tzdata

COPY backup.sh restore.sh entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/backup.sh /usr/local/bin/restore.sh /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
