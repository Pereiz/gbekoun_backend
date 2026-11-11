#!/bin/sh
# Planifie backup.sh avec cron (BACKUP_CRON, par défaut tous les jours à 3 h).
set -eu

: "${BACKUP_CRON:=0 3 * * *}"

# crond ne transmet pas l'environnement du conteneur aux tâches : on l'enregistre.
export -p | grep -E 'export (PG_|PGPASSWORD|MONGO_|BACKUP_|TZ)' > /etc/backup.env
chmod 600 /etc/backup.env

echo "$BACKUP_CRON /usr/local/bin/backup.sh >> /proc/1/fd/1 2>&1" > /etc/crontabs/root
echo "Sauvegardes planifiées : « $BACKUP_CRON » (conservation : ${BACKUP_RETENTION_DAYS:-14} jours)"

if [ "${BACKUP_ON_START:-0}" = "1" ]; then
    /usr/local/bin/backup.sh || echo "Première sauvegarde en échec (voir ci-dessus)"
fi

exec crond -f -l 8
