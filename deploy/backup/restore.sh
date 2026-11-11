#!/bin/sh
# Restaure une sauvegarde. ⚠️ REMPLACE les données actuelles.
#
#   docker compose -f docker-compose.prod.yml exec backup restore.sh            # liste
#   docker compose -f docker-compose.prod.yml stop api                            # couper l'API d'abord
#   docker compose -f docker-compose.prod.yml exec backup restore.sh 20261112_030000 --yes
#   docker compose -f docker-compose.prod.yml start api
set -eu

[ -f /etc/backup.env ] && . /etc/backup.env
: "${PG_HOST:=postgres}" "${PG_PORT:=5432}" "${MONGO_HOST:=mongo}" "${MONGO_PORT:=27017}"

if [ $# -lt 1 ]; then
    echo "Sauvegardes disponibles :"
    ls -1 /backups | grep '^20' || echo "  (aucune)"
    echo "Usage : restore.sh <AAAAMMJJ_HHMMSS> --yes"
    exit 1
fi

source_dir="/backups/$1"
[ -d "$source_dir" ] || { echo "Sauvegarde introuvable : $1"; exit 1; }
[ "${2:-}" = "--yes" ] || { echo "Ajoutez --yes pour confirmer : les données actuelles seront remplacées."; exit 1; }

echo "Vérification de l'intégrité…"
(cd "$source_dir" && sha256sum -c SHA256SUMS)

echo "PostgreSQL…"
PGPASSWORD="$PG_PASSWORD" pg_restore -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d "$PG_DATABASE" \
    --clean --if-exists --no-owner "$source_dir/postgres.dump"

echo "MongoDB…"
mongorestore --host "$MONGO_HOST" --port "$MONGO_PORT" \
    --username "$MONGO_ROOT_USER" --password "$MONGO_ROOT_PASSWORD" --authenticationDatabase admin \
    --nsInclude "$MONGO_DB.*" --drop --archive="$source_dir/mongo.archive.gz" --gzip --quiet

echo "Photos de profil…"
find /uploads -mindepth 1 -delete
tar -xzf "$source_dir/uploads.tar.gz" -C /uploads

echo "Restauration de $1 terminée. Redémarrez l'API."
