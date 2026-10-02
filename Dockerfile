FROM python:3.11-slim

WORKDIR /app

# Installer les dépendances système nécessaires
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copier les requirements et installer les dépendances Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copier toute l'application
COPY . .

# Exposer le port 8880 (ton port Flask)
EXPOSE 8880

# Variable d'environnement pour Flask
ENV FLASK_APP=app.py
ENV FLASK_ENV=production

# Gunicorn gthread prend en charge Socket.IO en mode threading.
CMD ["sh", "-c", "exec gunicorn --worker-class gthread --workers 1 --threads 100 --bind 0.0.0.0:${PORT:-8880} app:app"]

# COPY entrypoint.sh /entrypoint.sh
# RUN chmod +x /entrypoint.sh
# ENTRYPOINT ["/entrypoint.sh"]