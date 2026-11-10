# Démarrer le backend GBEKOUN

## Vue d'ensemble

Le backend est composé de **3 parties** :

| Partie | Rôle | Port local |
|--------|------|-----------|
| **API Flask + Socket.IO** (`app.py`) | Routes REST `/api/...`, temps réel `/socket.io/`, doc Swagger `/apidocs/` | `8880` |
| **PostgreSQL** (schéma `gbekoun`) | Utilisateurs, conversations, participants, codes d'invitation, blocages, signalements, jetons push | `5435` (Docker) |
| **MongoDB** (base `gbekoundb_messages`) | Contenu des messages (texte, réponses, réactions, suppressions) | `27020` (Docker) |

Prérequis : **Docker** + **Docker Compose**, et pour le mode « Python local » :
**Python 3.11+**.

Trois façons de démarrer :

- [Option A — Tout dans Docker](#option-a--tout-dans-docker-le-plus-simple) (le plus simple)
- [Option B — API en Python local, bases dans Docker](#option-b--api-en-python-local-bases-dans-docker) (le plus pratique pour développer)
- [Production (Render)](#production-render)

---

## 1. Préparer le fichier `.env` (toutes options)

```bash
cp .env.example .env
```

Puis remplir au minimum :

```ini
FLASK_DEBUG=1
SECRET_KEY=<valeur aléatoire>
JWT_SECRET=<autre valeur aléatoire>
PG_PASSWORD=<mot de passe de ton choix>
MONGO_ROOT_PASSWORD=<mot de passe de ton choix>
MONGO_URI=mongodb://gbekoun_admin:<MONGO_ROOT_PASSWORD>@localhost:27020/gbekoundb_messages?authSource=admin
```

Générer une valeur aléatoire :

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

> Ton `.env` actuel a été écrit pour l'ancienne configuration : compare-le
> avec `.env.example` et ajoute `FLASK_DEBUG`, `MONGO_ROOT_USER`,
> `MONGO_ROOT_PASSWORD`. Les variables `PG_SCHEMA`, `UPLOAD_FOLDER`,
> `MAX_FILE_SIZE`, `ALLOWED_EXTENSIONS` et `SOCKETIO_ASYNC_MODE` ne sont plus lues.

> ⚠️ **Conflit de ports avec tes anciens conteneurs.** Les conteneurs
> `postgres_chat` (5435) et `mongodb_chat` (27020) de l'ancien
> `docker-compose-dev.yml` utilisent les mêmes ports. Soit tu les arrêtes
> (`docker stop postgres_chat mongodb_chat`), soit tu **continues à les
> utiliser** à la place des nouveaux : dans ce cas passe directement à
> l'[option B](#option-b--api-en-python-local-bases-dans-docker) en gardant
> tes identifiants actuels dans `.env`.

---

## Option A — Tout dans Docker (le plus simple)

```bash
docker compose up --build
```

Au **premier** démarrage :
- PostgreSQL exécute `database/postgres/schema.sql` (création du schéma et des tables) ;
- MongoDB exécute `database/mongo/init.js` (collection `messages`, validation, index).

Ces scripts ne sont exécutés que si les volumes sont vides. Pour tout
réinitialiser (⚠️ efface les données locales) :

```bash
docker compose down -v
```

Vérifier :

```bash
curl http://localhost:8880/health          # {"status": "ok"}
curl http://localhost:8880/api/health      # état PostgreSQL + MongoDB
```

Puis créer le premier administrateur ([étape 2](#2-créer-le-premier-administrateur)) :

```bash
docker compose exec api python -m scripts.create_admin --phone +22990000000 --name "Admin"
```

Commandes utiles :

```bash
docker compose logs -f api        # logs de l'API
docker compose restart api        # après une modification du code (pas de rechargement auto)
docker compose down               # tout arrêter (les données sont conservées)
```

---

## Option B — API en Python local, bases dans Docker

Pratique pour développer : rechargement automatique, débogueur, etc.

**1. Démarrer seulement les bases :**

```bash
docker compose up -d postgres mongo
```

**2. Installer les dépendances (une seule fois) :**

```bash
python3 -m venv .genv
source .genv/bin/activate
pip install -r requirements.txt
```

**3. Lancer l'API :**

```bash
source .genv/bin/activate
python app.py
```

L'API écoute sur `http://localhost:8880`. Dans `.env`, `PG_HOST=localhost`,
`PG_PORT=5435` et `MONGO_URI=...@localhost:27020/...` pointent vers les
conteneurs.

### Avec une base existante (anciens conteneurs, autre serveur)

Le schéma est **idempotent** : il peut être rejoué sur une base existante pour
ajouter ce qui manque, sans rien effacer.

```bash
psql "postgresql://<user>:<mot_de_passe>@localhost:5435/gbekoundb" -f database/postgres/schema.sql
mongosh "<MONGO_URI>" database/mongo/init.js
```

---

## 2. Créer le premier administrateur

L'inscription exige un code d'invitation, et seul un administrateur peut en
générer. Il faut donc créer le premier admin à la main :

```bash
python -m scripts.create_admin --phone +22990000000 --name "Admin GBEKOUN"
# Base : gbekoundb_user@localhost:5435/gbekoundb      ← vérifier que c'est la bonne base !
# Compte créé : +22990000000 (id ...)
# Code d'activation de l'appareil : K7MP-2XQR  (valable 24 h, usage unique)
```

Si le numéro existe déjà, le compte est promu administrateur et un nouveau
code est généré : c'est aussi la procédure de secours si l'admin perd son téléphone.

**Chaque compte est lié à un seul appareil** (voir `docs/CORRECTIONS.md`,
point 1). Chaque appel de connexion envoie donc un `device_secret` : une clé
aléatoire propre à l'appareil, générée une fois puis réutilisée. Pour des
tests avec curl, génère-en une et garde-la :

```bash
SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
```

Ensuite :

```bash
# 1. Activer l'appareil de l'admin avec le code affiché par le script
curl -X POST http://localhost:8880/api/auth/device/activate \
     -H "Content-Type: application/json" \
     -d "{\"phone_number\": \"+22990000000\", \"activation_code\": \"K7MP-2XQR\",
          \"device_secret\": \"$SECRET\", \"device_name\": \"PC admin\", \"platform\": \"android\"}"
# → {"token": "eyJ...", "device_id": "...", ...}

# Les fois suivantes : connexion normale (même SECRET)
curl -X POST http://localhost:8880/api/auth/login \
     -H "Content-Type: application/json" \
     -d "{\"phone_number\": \"+22990000000\", \"device_secret\": \"$SECRET\"}"

# 2. Générer des codes d'invitation
curl -X POST http://localhost:8880/api/auth/invite-codes \
     -H "Authorization: Bearer <token>" \
     -H "Content-Type: application/json" \
     -d '{"count": 5, "expires_days": 30}'

# 3. Un utilisateur s'inscrit avec un code (son appareil est enregistré au passage)
curl -X POST http://localhost:8880/api/auth/register \
     -H "Content-Type: application/json" \
     -d '{"phone_number": "+22991111111", "invite_code": "A1B2C3", "display_name": "Koffi",
          "device_secret": "<secret de son appareil>", "device_name": "Tecno Spark", "platform": "android"}'
```

**Un utilisateur a perdu son téléphone** : retrouver son `user_id` (`GET /api/admin/users?search=+229...`),
puis `POST /api/admin/users/<user_id>/activation-code` et lui transmettre le
code **en personne ou en le rappelant soi-même**. Téléphone volé : d'abord
`POST /api/admin/users/<user_id>/devices/revoke`.

---

## 3. Explorer l'API

- **Swagger (toutes les routes, testables)** : <http://localhost:8880/apidocs/>
  → bouton *Authorize* → `Bearer <token>`.
- **Configuration client** : `GET /api/config`
- **Infos WebSocket** (événements disponibles) : `GET /api/websocket/info`

### Connexion temps réel (Socket.IO)

```javascript
import { io } from "socket.io-client";

const socket = io("http://localhost:8880", {
  transports: ["websocket"],
  query: { token: "<JWT>" },
});

socket.on("connected", (data) => console.log("connecté", data));
socket.on("new_message", (msg) => console.log("nouveau message", msg));
socket.on("error", (err) => console.warn(err.code, err.message));

socket.emit("send_message", {
  conversation_id: "<uuid>",
  type: "text",
  content: "Bonjour !",
});
```

| Client → serveur | Serveur → client |
|------------------|------------------|
| `send_message`, `edit_message`, `delete_message` | `new_message`, `message_sent`, `message_edited`, `message_deleted` |
| `typing`, `mark_read` | `user_typing`, `message_read` |
| `react`, `remove_reaction` | `reaction_added`, `reaction_removed` |
| `ping` | `pong`, `connected`, `user_presence`, `error` |
| | `device_approval_requested` (un nouvel appareil demande l'accord) |

---

## 4. Lancer les tests

Aucune base n'est nécessaire (PostgreSQL et MongoDB sont simulés) :

```bash
source .genv/bin/activate
FLASK_DEBUG=1 python tests/e2e/test_message_flow.py -v
```

---

## Production (Render)

Render construit l'image à partir du **`Dockerfile`** (le `docker-compose.yml`
n'est pas utilisé). La commande lancée est :

```
gunicorn --worker-class gthread --workers 1 --threads 100 --bind 0.0.0.0:$PORT app:app
```

> Garder **un seul worker** : l'état des connexions Socket.IO est en mémoire
> du process. Pour passer à plusieurs workers/instances, il faudra un message
> queue (Redis) pour Socket.IO.

**Variables d'environnement à définir sur Render :**

| Variable | Obligatoire | Exemple / remarque |
|----------|:-----------:|--------------------|
| `SECRET_KEY` | ✅ | valeur aléatoire (le démarrage échoue sans) |
| `JWT_SECRET` | ✅ | valeur aléatoire (le démarrage échoue sans) |
| `PG_PASSWORD` | ✅ | mot de passe de la base Render (le démarrage échoue sans) |
| `PG_HOST` | ✅ | hôte interne Render (`dpg-...`) |
| `PG_PORT` | | `5432` (défaut) |
| `PG_DATABASE` | | `gbekoundb` (défaut) |
| `PG_USER` | | `gbekoundb_user` (défaut) |
| `MONGO_URI` | ✅ | URI MongoDB Atlas (`mongodb+srv://...`) |
| `MONGO_DB` | | `gbekoundb_messages` (défaut) |
| `EXPO_ACCESS_TOKEN` | | si l'accès sécurisé aux push Expo est activé |
| `FLASK_DEBUG` | | ne pas définir (ou `0`) en production |

**Mettre à jour le schéma de la base de production** (ajoute ce qui manque,
n'efface rien) :

```bash
psql "<External Database URL de Render>" -f database/postgres/schema.sql
mongosh "<MONGO_URI Atlas>" database/mongo/init.js
```

**Sauvegardes** : la route `POST /api/admin/backup/create` nécessite
`pg_dump` et `mongodump` sur le serveur (absents de l'image Docker : elle
répond alors `501`). Sur Render, utiliser plutôt les sauvegardes automatiques
de la base managée, et celles d'Atlas pour MongoDB.

---

## Dépannage

| Symptôme | Cause probable |
|----------|----------------|
| `RuntimeError: Variables d'environnement manquantes : ...` | `SECRET_KEY` / `JWT_SECRET` / `PG_PASSWORD` absents. En local, mettre `FLASK_DEBUG=1` ou les définir. |
| `Bind for 0.0.0.0:5435 failed: port is already allocated` | Les anciens conteneurs `postgres_chat` / `mongodb_chat` tournent : `docker stop postgres_chat mongodb_chat`. |
| `relation "gbekoun.users" does not exist` | Le schéma n'a pas été appliqué : relancer `schema.sql` (ou `docker compose down -v` puis `up`). |
| `Authentication failed` côté MongoDB | `MONGO_URI` ne correspond pas à `MONGO_ROOT_USER` / `MONGO_ROOT_PASSWORD`, ou le volume Mongo a été créé avec d'autres identifiants (`docker compose down -v`). |
| `401 Compte introuvable ou supprimé` avec un token valide | Le compte a été supprimé, ou le token vient d'une autre base. |
| `401 DEVICE_REVOKED` | Un autre appareil a été activé sur ce compte (ou l'admin a révoqué celui-ci). Réactiver l'appareil. |
| `403 DEVICE_NOT_RECOGNIZED` au login | Ce `device_secret` n'est pas celui de l'appareil du compte : passer par la validation de l'ancien appareil ou un code admin. |
| Base existante créée avant le 11/11/2026 | Appliquer `database/migrations/002_user_devices.sql` (ou rejouer `schema.sql`). |
| Socket connecté mais aucun message reçu | Vérifier que le token est passé dans `query: { token }` et que l'utilisateur est membre actif de la conversation. |
