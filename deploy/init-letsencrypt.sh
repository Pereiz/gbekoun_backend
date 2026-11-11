#!/usr/bin/env bash
# Obtient le premier certificat HTTPS Let's Encrypt (à lancer UNE fois sur le serveur).
# Le renouvellement est ensuite automatique (services certbot + nginx).
#
# Prérequis : le nom de domaine API_DOMAIN pointe vers ce serveur, ports 80 et
# 443 ouverts, .env rempli (API_DOMAIN, LETSENCRYPT_EMAIL, mots de passe).
#
#   ./deploy/init-letsencrypt.sh            # certificat réel
#   LETSENCRYPT_STAGING=1 ./deploy/init-letsencrypt.sh   # essai (certificat de test, sans quota)
#   ./deploy/init-letsencrypt.sh --force    # recommencer même si un certificat existe
set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE=(docker compose -f docker-compose.prod.yml)

env_value() {  # lit une variable dans .env sans exécuter le fichier
    grep -E "^$1=" .env | tail -n1 | cut -d= -f2- | sed -e 's/^["'\'']//' -e 's/["'\'']$//'
}

[ -f .env ] || { echo "Fichier .env introuvable (cp .env.example .env)"; exit 1; }
DOMAIN="${API_DOMAIN:-$(env_value API_DOMAIN)}"
EMAIL="${LETSENCRYPT_EMAIL:-$(env_value LETSENCRYPT_EMAIL)}"
CERT_DIR="${CERTBOT_DIR:-./deploy/certbot}"
[ -n "$DOMAIN" ] || { echo "API_DOMAIN manquant dans .env"; exit 1; }
[ -n "$EMAIL" ] || { echo "LETSENCRYPT_EMAIL manquant dans .env"; exit 1; }

if [ -d "$CERT_DIR/conf/live/$DOMAIN" ] && [ "${1:-}" != "--force" ]; then
    echo "Un certificat existe déjà pour $DOMAIN (relancer avec --force pour le remplacer)."
    exit 0
fi

mkdir -p "$CERT_DIR/conf/live/$DOMAIN" "$CERT_DIR/www"

echo "1/4 Certificat temporaire (pour pouvoir démarrer nginx)…"
"${COMPOSE[@]}" run --rm --entrypoint "\
  openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
    -keyout /etc/letsencrypt/live/$DOMAIN/privkey.pem \
    -out /etc/letsencrypt/live/$DOMAIN/fullchain.pem \
    -subj /CN=localhost" certbot

echo "2/4 Démarrage de nginx…"
"${COMPOSE[@]}" up -d nginx

echo "3/4 Demande du certificat Let's Encrypt pour $DOMAIN…"
"${COMPOSE[@]}" run --rm --entrypoint "\
  rm -rf /etc/letsencrypt/live/$DOMAIN /etc/letsencrypt/archive/$DOMAIN \
         /etc/letsencrypt/renewal/$DOMAIN.conf" certbot

STAGING_ARG=""
[ "${LETSENCRYPT_STAGING:-0}" = "1" ] && STAGING_ARG="--staging"

"${COMPOSE[@]}" run --rm --entrypoint "\
  certbot certonly --webroot -w /var/www/certbot $STAGING_ARG \
    -d $DOMAIN --email $EMAIL --rsa-key-size 4096 --agree-tos --no-eff-email" certbot

echo "4/4 Rechargement de nginx…"
"${COMPOSE[@]}" exec nginx nginx -s reload

echo "Terminé : https://$DOMAIN"
