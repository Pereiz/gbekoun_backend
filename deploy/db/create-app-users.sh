#!/usr/bin/env bash
# Crée (ou met à jour) les comptes de l'application à droits limités sur des
# bases DÉJÀ existantes. Inutile sur une installation neuve : c'est fait
# automatiquement au premier démarrage.
#
#   ./deploy/db/create-app-users.sh                          # production
#   ./deploy/db/create-app-users.sh docker-compose.dev.yml   # développement
#
# Puis redémarrer l'API : docker compose -f <fichier> up -d api
# Rejouable sans risque (met à jour mots de passe et droits).
#
# Les scripts sont envoyés depuis le dépôt (et non lus dans le conteneur) :
# un fichier monté seul garde son ancienne version après un « git pull ».
set -euo pipefail

cd "$(dirname "$0")/../.."
COMPOSE=(docker compose -f "${1:-docker-compose.prod.yml}")

echo "PostgreSQL : compte applicatif…"
"${COMPOSE[@]}" exec -T postgres sh -c \
  'psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
        -v app_user="$PG_APP_USER" -v app_password="$PG_APP_PASSWORD" -f -' \
  < database/postgres/app_user.sql

echo "MongoDB : compte applicatif…"
"${COMPOSE[@]}" exec -T mongo sh -c \
  'mongosh --quiet -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" --eval "$(cat)"' \
  < database/mongo/init.js

echo "Terminé. Redémarrer l'API pour qu'elle utilise ces comptes."
