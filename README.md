# gbekoun_backend

Backend de l'application de messagerie **GBEKOUN** : API REST Flask, temps réel
Socket.IO, PostgreSQL (comptes, conversations) et MongoDB (messages).

- 🚀 **Démarrer le projet** : [docs/DEMARRAGE.md](docs/DEMARRAGE.md)
- 🛠️ **Corrections et réorganisation du 10/11/2026** : [docs/CORRECTIONS.md](docs/CORRECTIONS.md)
- 📚 **Documentation de l'API** : `http://localhost:8880/apidocs/` une fois lancé

```bash
cp .env.example .env     # puis remplir les valeurs
docker compose -f docker-compose.dev.yml up --build     # développement
docker compose -f docker-compose.prod.yml up -d --build # production (voir docs/DEMARRAGE.md)
```
