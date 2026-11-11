# Démarrer le backend GBEKOUN

## Vue d'ensemble

Le backend est composé de **3 parties** :

| Partie | Rôle | Port local |
|--------|------|-----------|
| **API Flask + Socket.IO** (`app.py`) | Routes REST `/api/...`, temps réel `/socket.io/`, doc Swagger `/apidocs/` | `8880` |
| **PostgreSQL** (schéma `gbekoun`) | Utilisateurs, appareils, conversations, participants, codes d'invitation, blocages, signalements, jetons push | `5435` (Docker) |
| **MongoDB** (base `gbekoundb_messages`) | Contenu des messages (texte, réponses, réactions, suppressions) | `27020` (Docker) |
| **Fichiers** (`UPLOAD_FOLDER`, volume `uploads_data`) | Photos de profil (JPEG 512×512) | — |

Prérequis : **Docker** + **Docker Compose**, et pour le mode « Python local » :
**Python 3.11+**.

Trois façons de démarrer :

- [Option A — Tout dans Docker](#option-a--tout-dans-docker-le-plus-simple) (le plus simple)
- [Option B — API en Python local, bases dans Docker](#option-b--api-en-python-local-bases-dans-docker) (le plus pratique pour développer)
- [Production (serveur perso, tout en Docker)](#production-serveur-perso-tout-en-docker)

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

> ⚠️ **Mots de passe : uniquement lettres, chiffres, `-` et `_`** (ce que
> produit la commande ci-dessus). Docker Compose interprète les `$` du `.env`
> (un mot de passe `abc$def` devient silencieusement `abc`), et les
> caractères `@ : / ? #` cassent l'adresse de connexion MongoDB construite à
> partir du mot de passe.

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
docker compose -f docker-compose.dev.yml up --build
```

Le code du projet est monté dans le conteneur : **toute modification recharge
l'API automatiquement**. Swagger : <http://localhost:8880/apidocs/>.

Au **premier** démarrage :
- PostgreSQL exécute `database/postgres/schema.sql` (création du schéma et des tables) ;
- MongoDB exécute `database/mongo/init.js` (collection `messages`, validation, index).

Ces scripts ne sont exécutés que si les volumes sont vides. Pour tout
réinitialiser (⚠️ efface les données locales) :

```bash
docker compose -f docker-compose.dev.yml down -v
```

Vérifier :

```bash
curl http://localhost:8880/health          # {"status": "ok"}
curl http://localhost:8880/api/health      # état PostgreSQL + MongoDB
```

Puis créer le premier administrateur ([étape 2](#2-créer-le-premier-administrateur)) :

```bash
docker compose -f docker-compose.dev.yml exec api python -m scripts.create_admin --phone +22990000000 --name "Admin"
```

Commandes utiles :

```bash
docker compose -f docker-compose.dev.yml logs -f api   # logs de l'API
docker compose -f docker-compose.dev.yml down          # tout arrêter (les données sont conservées)
```

---

## Option B — API en Python local, bases dans Docker

Pratique pour développer : rechargement automatique, débogueur, etc.

**1. Démarrer seulement les bases :**

```bash
docker compose -f docker-compose.dev.yml up -d postgres mongo
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

**L'admin sur son PC** (administration uniquement, session de 1 h) : le PC
demande l'accès, le téléphone de l'admin valide. Pas de code nécessaire.

```bash
PC_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
# 1. Le PC demande l'accès → le téléphone de l'admin reçoit une notification
curl -X POST http://localhost:8880/api/auth/device/web/requests \
     -H "Content-Type: application/json" \
     -d "{\"phone_number\": \"+22990000000\", \"device_secret\": \"$PC_SECRET\", \"device_name\": \"Chrome - Windows\"}"
# → {"request_id": "...", "status": "pending"}
# 2. L'admin accepte sur son téléphone (ou : POST /api/auth/device/requests/<id>/approve avec le token du téléphone)
# 3. Le PC récupère son token (valable 1 h)
curl -X POST http://localhost:8880/api/auth/device/requests/<request_id>/status \
     -H "Content-Type: application/json" -d "{\"device_secret\": \"$PC_SECRET\"}"
```

Téléphone de l'admin indisponible : un code (`create_admin` ou un autre admin)
s'utilise avec `POST /api/auth/device/web/activate`.

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

## Production (serveur perso, tout en Docker)

> Render n'est plus utilisé (décision du 11/11/2026). Tout tourne sur ton
> serveur avec `docker-compose.prod.yml`.

```
Internet ──443/80──▶ nginx ──▶ api ──▶ postgres / mongo     (réseau interne Docker)
                       ▲                  ▲
                    certbot            backup (cron)
```

| Service | Rôle |
|---------|------|
| `nginx` | Seul service exposé. HTTPS, redirection HTTP → HTTPS, WebSocket, limitation de débit, en-têtes de sécurité, Swagger fermé. |
| `certbot` | Certificat Let's Encrypt, renouvelé automatiquement (nginx le recharge toutes les 6 h). |
| `api` | Gunicorn (1 worker, 100 threads), `TRUST_PROXY=1`. |
| `postgres`, `mongo` | **Non exposés** sur Internet. Volumes `postgres_data`, `mongo_data`. |
| `backup` | Sauvegarde quotidienne PostgreSQL + MongoDB + photos, conservation 14 jours. |

Tous les services redémarrent automatiquement (`unless-stopped`) et leurs logs
tournent (10 Mo × 5 fichiers par service).

### Première installation

Prérequis sur le serveur : Docker + Docker Compose, un **nom de domaine**
(ex. `api.gbekoun.example`) qui pointe vers l'IP du serveur, ports **80 et
443** ouverts.

```bash
git clone <dépôt> gbekoun_backend && cd gbekoun_backend
cp .env.example .env
# Remplir .env : FLASK_DEBUG=0, SECRET_KEY, JWT_SECRET, PG_*, MONGO_ROOT_*,
#                API_DOMAIN, LETSENCRYPT_EMAIL, CORS_ORIGINS (domaine de l'admin web)

# 1. Construire les images
docker compose -f docker-compose.prod.yml build

# 2. Obtenir le certificat HTTPS (une seule fois ; essai possible avec LETSENCRYPT_STAGING=1)
./deploy/init-letsencrypt.sh

# 3. Tout démarrer
docker compose -f docker-compose.prod.yml up -d

# 4. Créer le premier administrateur (affiche un code d'activation de 24 h)
docker compose -f docker-compose.prod.yml exec api python -m scripts.create_admin --phone +229XXXXXXXX --name "Admin"
```

Vérifier : `curl https://<API_DOMAIN>/api/health`.

L'appli mobile utilise `https://<API_DOMAIN>` pour l'API et `wss://<API_DOMAIN>`
pour Socket.IO (l'API l'annonce correctement grâce à `TRUST_PROXY`).

### Mettre à jour

```bash
git pull
docker compose -f docker-compose.prod.yml up -d --build
```

Si le schéma a changé (nouvelle table, nouvelle colonne) — rejouable sans risque :

```bash
docker compose -f docker-compose.prod.yml exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < database/postgres/schema.sql
docker compose -f docker-compose.prod.yml exec -T mongo sh -c 'mongosh --quiet -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" /docker-entrypoint-initdb.d/01_init.js'
```

### Sauvegardes

- Automatiques chaque jour à 3 h (`BACKUP_CRON`), conservées 14 jours
  (`BACKUP_RETENTION_DAYS`), dans `./backups/` sur le serveur (`BACKUP_HOST_DIR`).
- Chaque sauvegarde : `postgres.dump`, `mongo.archive.gz`, `uploads.tar.gz`
  (photos) et `SHA256SUMS` (vérification d'intégrité).
- **Copier régulièrement `./backups/` hors du serveur** (autre machine,
  stockage externe) : une sauvegarde sur le même disque ne protège pas
  d'une panne de ce disque.

```bash
docker compose -f docker-compose.prod.yml exec backup backup.sh          # sauvegarde immédiate
docker compose -f docker-compose.prod.yml exec backup restore.sh         # liste les sauvegardes
docker compose -f docker-compose.prod.yml stop api
docker compose -f docker-compose.prod.yml exec backup restore.sh 20261112_030000 --yes
docker compose -f docker-compose.prod.yml start api
```

La restauration vérifie d'abord l'intégrité (`SHA256SUMS`) et **remplace**
les données actuelles (d'où `--yes`).

### Limitation de débit (nginx)

| Zone | Limite par adresse IP |
|------|-----------------------|
| `/api/auth/` (connexion, inscription, activation, codes) | 30 / minute, rafale de 20 |
| Reste de l'API (dont l'attente de validation d'appareil) | 20 / seconde, rafale de 60 |
| Socket.IO | non limité (connexions longues), 50 connexions simultanées par IP |

Au-delà : `429`. Réglages larges car beaucoup d'abonnés mobiles partagent une
même IP chez leur opérateur ; à ajuster dans
`deploy/nginx/templates/gbekoun.conf.template` si besoin.

### Variables d'environnement

| Variable | Obligatoire | Remarque |
|----------|:-----------:|----------|
| `SECRET_KEY`, `JWT_SECRET` | ✅ | valeurs aléatoires (`secrets.token_hex(32)`) |
| `PG_DATABASE`, `PG_USER`, `PG_PASSWORD` | ✅ | compte PostgreSQL créé au premier démarrage |
| `MONGO_ROOT_USER`, `MONGO_ROOT_PASSWORD` | ✅ | compte MongoDB créé au premier démarrage |
| `API_DOMAIN`, `LETSENCRYPT_EMAIL` | ✅ | domaine de l'API, e-mail d'alerte Let's Encrypt |
| `CORS_ORIGINS` | | domaine(s) de l'interface admin web |
| `MONGO_DB` | | `gbekoundb_messages` |
| `BACKUP_CRON`, `BACKUP_RETENTION_DAYS`, `BACKUP_HOST_DIR`, `TZ` | | `0 3 * * *`, `14`, `./backups`, `Africa/Porto-Novo` |
| `POSTGRES_IMAGE`, `MONGO_IMAGE` | | `postgres:16-alpine`, `mongo:8.0` |
| `HTTP_PORT`, `HTTPS_PORT` | | `80`, `443` |
| `EXPO_ACCESS_TOKEN` | | si l'accès sécurisé aux push Expo est activé |

`FLASK_DEBUG`, `TRUST_PROXY`, `PG_HOST`, `MONGO_URI` et `UPLOAD_FOLDER` sont
fixés par `docker-compose.prod.yml`.

> La route `POST /api/admin/backup/create` répond `501` dans ce déploiement :
> les sauvegardes sont faites par le service `backup`.

---

## Dépannage

| Symptôme | Cause probable |
|----------|----------------|
| `RuntimeError: Variables d'environnement manquantes : ...` | `SECRET_KEY` / `JWT_SECRET` / `PG_PASSWORD` absents. En local, mettre `FLASK_DEBUG=1` ou les définir. |
| `Bind for 0.0.0.0:5435 failed: port is already allocated` | Les anciens conteneurs `postgres_chat` / `mongodb_chat` tournent : `docker stop postgres_chat mongodb_chat`. |
| `relation "gbekoun.users" does not exist` | Le schéma n'a pas été appliqué : relancer `schema.sql` (ou `docker compose -f docker-compose.dev.yml down -v` puis `up`). |
| `Authentication failed` côté MongoDB | `MONGO_URI` ne correspond pas à `MONGO_ROOT_USER` / `MONGO_ROOT_PASSWORD`, ou le volume Mongo a été créé avec d'autres identifiants (`down -v`). |
| `401 Compte introuvable ou supprimé` avec un token valide | Le compte a été supprimé, ou le token vient d'une autre base. |
| `401 DEVICE_REVOKED` | Un autre appareil a été activé sur ce compte (ou l'admin a révoqué celui-ci). Réactiver l'appareil. |
| `401 SESSION_EXPIRED` (PC) | L'heure de session est écoulée sans prolongation confirmée sur le téléphone : se reconnecter. |
| `401 SESSION_IDLE` (PC) | Aucune requête du PC depuis 5 min : se reconnecter. |
| `403 WEB_ADMIN_ONLY` | Appel d'une route hors administration depuis un PC (normal), ou compte non admin qui tente d'ouvrir une session PC. |
| L'interface admin web est bloquée par le navigateur (erreur CORS) | Son domaine n'est pas dans `CORS_ORIGINS`. |
| `403 DEVICE_NOT_RECOGNIZED` au login | Ce `device_secret` n'est pas celui de l'appareil du compte : passer par la validation de l'ancien appareil ou un code admin. |
| Mot de passe refusé alors qu'il est bon | Il contient `$` (interprété par Docker Compose) ou `@ : / ? #` (casse l'adresse MongoDB) : en choisir un fait de lettres, chiffres, `-`, `_`. |
| `429 Too Many Requests` (production) | Limitation de débit nginx : attendre, ou ajuster les zones dans `deploy/nginx/templates/gbekoun.conf.template`. |
| L'appli reçoit `ws://` au lieu de `wss://` | `TRUST_PROXY` doit valoir `1` derrière nginx (déjà le cas dans `docker-compose.prod.yml`). |
| Base existante créée avant le 11/11/2026 | Appliquer `database/migrations/002_user_devices.sql` (ou rejouer `schema.sql`). |
| Socket connecté mais aucun message reçu | Vérifier que le token est passé dans `query: { token }` et que l'utilisateur est membre actif de la conversation. |
