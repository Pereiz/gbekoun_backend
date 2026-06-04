#!/bin/bash
# entrypoint.sh

echo "Waiting for PostgreSQL to be ready..."
until pg_isready -h postgresql -p 5432 -U gbekoundb_user; do
  sleep 1
done

echo "Waiting for MongoDB to be ready..."
until mongosh --host mongodb --port 27017 --username admin --password admin123 --eval "db.adminCommand('ping')" &>/dev/null; do
  sleep 1
done

echo "Databases are ready! Starting Flask app..."

# Lancer l'application avec SocketIO
exec python app.py