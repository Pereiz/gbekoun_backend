#!/bin/sh
# Sauvegarde complète : PostgreSQL + MongoDB + photos de profil.
#   Automatique : planifiée par cron (voir entrypoint.sh).
#   Manuelle    : docker compose -f docker-compose.prod.yml exec backup backup.sh
#
# Résultat : /backups/<AAAAMMJJ_HHMMSS>/ avec postgres.dump, mongo.archive.gz,
# uploads.tar.gz et SHA256SUMS. Les sauvegardes plus vieilles que
# BACKUP_RETENTION_DAYS jours sont supprimées.
set -eu

[ -f /etc/backup.env ] && . /etc/backup.env

: "${PG_HOST:=postgres}" "${PG_PORT:=5432}" "${MONGO_HOST:=mongo}" "${MONGO_PORT:=27017}"
: "${BACKUP_RETENTION_DAYS:=14}"

stamp=$(date +%Y%m%d_%H%M%S)
target="/backups/$stamp"
work="/backups/.en_cours_$stamp"
mkdir -p "$work"
trap 'rm -rf "$work"' EXIT

echo "[$(date -Iseconds)] Sauvegarde $stamp : début"

PGPASSWORD="$PG_PASSWORD" pg_dump -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d "$PG_DATABASE" \
    --format=custom --file="$work/postgres.dump"

mongodump --host "$MONGO_HOST" --port "$MONGO_PORT" \
    --username "$MONGO_ROOT_USER" --password "$MONGO_ROOT_PASSWORD" --authenticationDatabase admin \
    --db "$MONGO_DB" --archive="$work/mongo.archive.gz" --gzip --quiet

tar -czf "$work/uploads.tar.gz" -C /uploads .

(cd "$work" && sha256sum postgres.dump mongo.archive.gz uploads.tar.gz > SHA256SUMS)

# Le dossier final n'apparaît qu'une fois la sauvegarde complète.
mv "$work" "$target"
trap - EXIT

find /backups -mindepth 1 -maxdepth 1 -type d -name '20*' -mtime +"$BACKUP_RETENTION_DAYS" \
    -exec rm -rf {} +

echo "[$(date -Iseconds)] Sauvegarde $stamp : terminée ($(du -sh "$target" | cut -f1))"
