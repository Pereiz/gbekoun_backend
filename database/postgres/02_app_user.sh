#!/bin/sh
# Exécuté automatiquement par l'image postgres au PREMIER démarrage (volume vide),
# après 01_schema.sql : crée le compte de l'application à droits limités.
# Pour une base déjà existante : deploy/db/create-app-users.sh
set -eu

if [ -z "${PG_APP_USER:-}" ] || [ -z "${PG_APP_PASSWORD:-}" ]; then
    echo "PG_APP_USER / PG_APP_PASSWORD absents : compte applicatif non créé." >&2
    exit 1
fi

psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     -v app_user="$PG_APP_USER" -v app_password="$PG_APP_PASSWORD" \
     -f /opt/gbekoun/app_user.sql
