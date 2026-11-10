FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Toutes les dépendances existent en wheels binaires : pas besoin de compilateur.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8880

# Un seul worker : l'état des connexions Socket.IO est gardé en mémoire du process.
# gthread prend en charge Socket.IO en mode threading.
CMD ["sh", "-c", "exec gunicorn --worker-class gthread --workers 1 --threads 100 --bind 0.0.0.0:${PORT:-8880} app:app"]
