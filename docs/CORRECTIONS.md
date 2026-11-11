# Corrections et réorganisation du backend — 10 novembre 2026

Ce document explique **ce qui a été changé, pourquoi, et où**. Il suit la
numérotation de l'audit : points 2 à 13 (10/11/2026), puis point 1 —
connexion liée à l'appareil (11/11/2026, voir
[Point 1](#point-1--connexion-liée-à-lappareil-11112026)).

> ⚠️ **Actions à faire de ton côté** avant/après le déploiement :
> voir la section [Actions manuelles obligatoires](#actions-manuelles-obligatoires).

---

## Sommaire

1. [Actions manuelles obligatoires](#actions-manuelles-obligatoires)
2. [Les corrections, une par une (points 2 à 13)](#les-corrections-une-par-une)
3. [Point 1 — connexion liée à l'appareil](#point-1--connexion-liée-à-lappareil-11112026)
3. [Réorganisation du projet](#réorganisation-du-projet)
4. [Fichiers supprimés](#fichiers-supprimés)
5. [Petites corrections faites au passage](#petites-corrections-faites-au-passage)
6. [Tests](#tests)
7. [Changements visibles par l'application mobile](#changements-visibles-par-lapplication-mobile)
8. [Reste à faire](#reste-à-faire)

---

## Actions manuelles obligatoires

| # | Action | Pourquoi |
|---|--------|----------|
| A | **Base Render** : Render n'est plus utilisé. Si l'ancienne base Render existe encore, la **supprimer** (ou au minimum changer son mot de passe : *Rotate password*). Ne jamais réutiliser ce mot de passe sur le nouveau serveur. | Il était écrit en clair dans `docker-compose.yml` et se trouve **dans l'historique Git poussé sur GitHub** (commits `5e9e294`, `27c934b`). Le retirer du fichier ne suffit pas. |
| B | Si le mot de passe `npg_…` présent dans l'ancien `gbekoundb.sql` est (ou a été) utilisé quelque part (Neon ?), le changer aussi. | Même raison : il est dans l'historique Git. |
| C | Vérifier que **`SECRET_KEY`, `JWT_SECRET` et `PG_PASSWORD`** sont définis dans le `.env` du serveur de production. | L'application **refuse désormais de démarrer** en production si l'un d'eux manque (point 5). Générer une valeur : `python -c "import secrets; print(secrets.token_hex(32))"`. |
| D | Vérifier en prod s'il existe un utilisateur `+1234567890` admin, et le supprimer/rétrograder. | L'ancien script SQL créait cet **administrateur par défaut**. Comme la connexion se fait par numéro seul, n'importe qui pouvait se connecter en admin avec ce numéro. Requête : `SELECT id, is_admin FROM gbekoun.users WHERE phone_number = '+1234567890';` |
| E | Vérifier l'index TTL MongoDB en prod : `db.messages.getIndexes()`. S'il y a `idx_ttl_timestamp`, **MongoDB supprime automatiquement tous les messages de plus d'un an**. Pour l'enlever : `db.messages.dropIndex("idx_ttl_timestamp")`. | L'ancien `gekoun_messages.js` le créait (commenté « optionnel » mais actif). Il n'est plus créé par le nouveau script. |
| F | Si `JWT_SECRET` change, tous les utilisateurs devront se reconnecter. | Les anciens tokens signés avec l'ancien secret deviennent invalides. C'est voulu si l'ancien secret était une valeur par défaut. |

Pour réécrire l'historique Git et effacer définitivement les secrets
(`git filter-repo`), il faut le faire en connaissance de cause (force-push,
clones existants) : ce n'est pas fait ici. La rotation des mots de passe
(A, B) suffit à neutraliser la fuite.

---

## Les corrections, une par une

### 2. Secrets en clair dans le dépôt

**Problème.** `docker-compose.yml` contenait l'hôte et le mot de passe de la
base Render de production. `gbekoundb.sql` contenait un autre mot de passe.

**Correction.**
- `docker-compose.yml` réécrit (devenu [docker-compose.dev.yml](../docker-compose.dev.yml), voir R5) : **aucun secret**, tout
  vient du fichier `.env` (non versionné). Il lance désormais un environnement
  local complet (API + PostgreSQL + MongoDB) au lieu de pointer vers la prod.
- `gbekoundb.sql` remplacé par [database/postgres/schema.sql](../database/postgres/schema.sql),
  sans `CREATE USER ... PASSWORD`.
- [.env.example](../.env.example) documente toutes les variables, sans valeur sensible.
- [.gitignore](../.gitignore) : `.env.*`, `render_db`, `backups/`, `*.dump` sont ignorés.

➡️ Voir aussi les actions manuelles A et B.

### 3. Transfert de message sans contrôle d'accès

**Problème.** `POST /api/messages/<conv>/messages/<id>/forward` ne vérifiait pas
que l'utilisateur était membre de la conversation **d'origine**. Avec un
identifiant de message, on pouvait le copier dans sa propre conversation et
donc le lire.

**Correction** ([blueprints/messages.py](../blueprints/messages.py), `forward_message`) :
vérification `is_participant(conversation_id, user)` avant toute lecture du
message source. Au passage, un message transféré :
- met à jour le compteur de non-lus, `updated_at` et envoie la notification push
  (il ne le faisait pas) ;
- est diffusé en temps réel (`new_message`) dans la conversation cible.

### 4. Comptes suspendus ou supprimés toujours actifs

**Problème.** Un compte suspendu ou supprimé pouvait continuer à utiliser son
token (valide 30 jours) et même se reconnecter.

**Correction.**
- [core/security.py](../core/security.py) — `require_auth` vérifie l'état du
  compte à chaque requête : supprimé → `401`, suspendu → `403 Compte suspendu`.
- `POST /api/auth/login` refuse les comptes supprimés (`404`) et suspendus (`403`).
- La connexion Socket.IO est refusée pour ces comptes.
- Quand un admin suspend, supprime un compte ou valide un signalement avec
  suspension, et quand un utilisateur supprime son compte, **ses sockets
  ouverts sont fermés immédiatement** (`core/realtime.disconnect_user`).

### 5. Secrets par défaut faibles

**Problème.** Sans variable d'environnement, `JWT_SECRET` valait
`'jwt-secret-key'` : n'importe qui pouvait fabriquer un token valide pour
n'importe quel utilisateur.

**Correction** ([core/config.py](../core/config.py), `validate_config`) :
- en production (`FLASK_DEBUG` absent ou `0`), l'application **refuse de
  démarrer** si `SECRET_KEY`, `JWT_SECRET` ou `PG_PASSWORD` manque, avec un
  message clair ;
- en développement (`FLASK_DEBUG=1`), des valeurs de secours sont utilisées
  avec un avertissement dans les logs ;
- plus aucun mot de passe de base de données par défaut dans le code.

### 6. Handlers Socket.IO écrasés

**Problème.** `app.py` redéclarait `connect`, `disconnect`, `typing` et
`mark_read` **après** avoir importé `sockets/events.py`. Avec Socket.IO, c'est
la dernière déclaration qui gagne (vérifié par un test). Conséquences :
- à la connexion, l'utilisateur rejoignait les rooms de **toutes** ses
  conversations, y compris les groupes quittés ou dont il avait été retiré :
  il continuait à recevoir les messages en temps réel ;
- les événements de présence `user_presence` n'étaient jamais envoyés ;
- `typing` / `mark_read` n'avaient aucun contrôle d'appartenance.

**Correction.**
- Tous les événements Socket.IO sont **uniquement** dans
  [sockets/events.py](../sockets/events.py). `app.py` ne fait que l'importer.
- À la connexion : seulement les conversations où `deleted_at IS NULL`.
- La présence (`user_presence`) est envoyée à la première connexion et à la
  dernière déconnexion d'un utilisateur (plusieurs appareils gérés).
- `typing` et `mark_read` ne sont acceptés que si le socket est dans la room
  de la conversation (donc membre actif) — sans requête SQL supplémentaire.
- `mark_read` valide l'ID de message (avant : exception si ID invalide).
- Les rooms sont maintenues à jour depuis l'API REST
  ([core/realtime.py](../core/realtime.py)) :
  - création de groupe, ajout de participant → les sockets des membres
    rejoignent la room immédiatement ;
  - retrait d'un participant, départ d'un groupe, suppression d'une
    conversation → les sockets quittent la room immédiatement.
- Le suivi des connexions (`sid` ↔ utilisateur) est protégé par un verrou
  (accès concurrents avec `--threads 100`).

### 7. Réactions par socket toujours en erreur

**Problème.** L'événement `react` faisait `$pull` et `$push` sur le champ
`reactions` dans la **même** requête : MongoDB refuse (« would create a
conflict »). Aucune réaction par socket ne fonctionnait.

**Correction.** Nouvelle fonction `set_user_reaction` dans
[utils/message_helpers.py](../utils/message_helpers.py) (pipeline d'agrégation,
une seule opération atomique), utilisée par la route REST **et** par le socket.
Les événements `react` / `remove_reaction` vérifient aussi désormais que
l'utilisateur est membre de la conversation (avant : n'importe qui pouvait
réagir à n'importe quel message), et on ne peut plus réagir à un message
supprimé.

### 8. Connexions PostgreSQL « empoisonnées »

**Problème.** `execute_query` ne faisait jamais de `rollback`. Après une
erreur SQL, la connexion retournait au pool dans l'état *transaction aborted* :
toutes les requêtes suivantes tombant sur cette connexion échouaient
(`current transaction is aborted…`) jusqu'au redémarrage.

**Correction** ([core/database.py](../core/database.py)) :
- nouveau gestionnaire `transaction()` : `commit` si tout passe, `rollback`
  sinon, et la connexion est toujours rendue au pool (fermée si elle est cassée) ;
- `execute_query` l'utilise ;
- le pool est créé à la première requête (l'import de l'app ne nécessite plus
  de base démarrée, ce qui permet de lancer les tests sans base).

### 9. `GET /api/auth/invite-codes?include_used=false` → erreur SQL

**Problème.** Il manquait un espace : la requête générée contenait
`... IS NULLORDER BY ...`.

**Correction** ([blueprints/auth.py](../blueprints/auth.py), `list_invite_codes`) :
`" ORDER BY ..."` avec l'espace. Les paramètres `count` / `expires_days` de la
génération de codes sont aussi validés (avant : un texte provoquait une erreur 500).

### 10. La mise en sourdine empêchait d'envoyer

**Problème.** Mettre une conversation en sourdine bloquait **l'envoi** de
messages (`403 Conversation en sourdine`). Une sourdine ne doit couper que les
notifications reçues.

**Correction.** Le contrôle est retiré de l'envoi (REST et socket). Les
notifications push ignoraient déjà les membres en sourdine
([core/notifications.py](../core/notifications.py)) : c'est le seul effet de la
sourdine, comme sur les messageries habituelles.

### 11. Supprimer une discussion directe la cassait en deux

**Problème.** Après « Supprimer la conversation », `create_direct` créait une
**nouvelle** conversation avec le même contact, tandis que le contact
continuait d'écrire dans l'ancienne, devenue invisible : ses messages étaient
perdus pour l'utilisateur.

**Correction** (comportement type WhatsApp) :
- **Suppression** ([blueprints/conversations.py](../blueprints/conversations.py),
  `delete_conversation`) : l'historique est effacé **pour l'utilisateur seul**
  (`deleted_for`), la conversation disparaît de sa liste, ses sockets quittent la room.
- **Nouveau message dans une discussion directe** ([services/messaging.py](../services/messaging.py),
  `after_message_created`) : la conversation **réapparaît** chez celui qui
  l'avait supprimée (vide de l'ancien historique), et ses appareils connectés
  rejoignent la room pour recevoir ce message.
- **`POST /api/conversations/direct`** retrouve la conversation existante du
  duo même si l'un des deux l'a supprimée (et la réactive pour celui qui la
  rouvre). Il ne peut donc plus y avoir deux conversations directes pour le même duo.
- On ne peut plus créer de conversation directe avec soi-même (avant : erreur 500).
- Les groupes ne sont **pas** concernés par la réactivation : un membre parti
  ou retiré ne revient pas tout seul.

### 12. Le backup ne sauvegardait pas les bases

**Problème.** `POST /api/admin/backup/create` construisait les commandes
`pg_dump` et `mongodump` mais **ne les exécutait jamais** : l'archive ne
contenait que le dossier `uploads`, tout en répondant « Backup créé avec succès ».

**Correction** ([blueprints/admin.py](../blueprints/admin.py), `create_backup`) :
- les commandes sont réellement exécutées (`subprocess.run`, sans shell,
  délai max 15 min) ;
- le mot de passe PostgreSQL passe par la variable `PGPASSWORD`, jamais dans
  la ligne de commande ;
- si `pg_dump` / `mongodump` ne sont pas installés sur le serveur, la route
  répond **`501`** avec un message explicite au lieu de faire semblant ;
- en cas d'échec : `500` (détail dans les logs), dossier temporaire nettoyé.

> L'image Docker de l'API n'embarque pas encore ces outils (à prévoir avec le
> déploiement sur le serveur perso, voir docs/DEMARRAGE.md).

### 13. Inscription et création de groupe non atomiques

**Problème.**
- Inscription : le code d'invitation était vérifié, puis l'utilisateur créé,
  puis le code marqué utilisé, en 3 requêtes séparées. Deux inscriptions
  simultanées pouvaient utiliser le même code ; une erreur au milieu laissait
  un compte créé sans code consommé.
- Création de groupe : un numéro en double (ou son propre numéro) dans la
  liste provoquait une erreur au milieu, laissant un groupe à moitié créé.

**Correction.**
- [blueprints/auth.py](../blueprints/auth.py), `register` : création du compte
  et consommation du code dans **une seule transaction**. Le code est réservé
  par un `UPDATE ... WHERE used_by IS NULL AND expires_at > NOW() RETURNING`
  : un seul des deux gagne, l'autre reçoit `403` et son compte n'est pas créé.
  Un numéro ou un nom d'utilisateur déjà pris renvoie `409`.
- [blueprints/conversations.py](../blueprints/conversations.py), `create_group` :
  tout dans une transaction, participants dédoublonnés, recherche des numéros
  en une seule requête (avant : une requête par numéro).

---

## Point 1 — connexion liée à l'appareil (11/11/2026)

**Problème.** `POST /api/auth/login` ne demandait que le numéro de téléphone :
n'importe qui connaissant un numéro (et la recherche d'utilisateurs les
affiche) obtenait un token valable 30 jours. Pas d'accès à un service SMS/OTP.

**Solution retenue : un seul appareil autorisé par compte.**

| Situation | Ce qui se passe |
|-----------|-----------------|
| Inscription | Le code d'invitation sert aussi d'activation : l'appareil utilisé devient l'appareil du compte. |
| Connexion | Numéro **+ secret de l'appareil**. Un autre appareil reçoit `403 DEVICE_NOT_RECOGNIZED`. |
| Changement de téléphone, ancien disponible | Le nouveau demande l'accord ; l'ancien reçoit une notification « Autoriser la connexion sur iPhone 13 ? » et accepte ou refuse. |
| Téléphone perdu / cassé | L'admin génère un **code d'activation** (8 caractères, usage unique, 30 min, 5 essais) et le transmet par un canal sûr. |
| Téléphone volé | L'admin **révoque** l'appareil : déconnexion immédiate. Puis code admin pour le nouveau. |

Dans tous les cas, dès qu'un nouvel appareil est activé, **l'ancien est
déconnecté immédiatement** : son token est refusé (`401 DEVICE_REVOKED`), ses
sockets sont fermés, ses jetons de notification push sont désactivés.

**Comment c'est sécurisé.**
- Le « device » n'est pas un identifiant matériel (copiable) mais une **clé
  secrète aléatoire** générée par l'appli et gardée dans le stockage sécurisé
  du téléphone. Le serveur n'en garde que l'empreinte SHA-256.
- Le token JWT contient l'identifiant de l'appareil (`did`). `require_auth` et
  la connexion Socket.IO vérifient à chaque fois que c'est **toujours**
  l'appareil actif (dans la même requête SQL que le contrôle suspendu/supprimé).
- La base de données garantit elle-même « un seul appareil actif par compte »
  (index unique partiel), même en cas de requêtes simultanées.
- Codes admin : stockés sous forme d'empreinte, usage unique, 30 minutes,
  5 essais, un nouveau code annule le précédent.
- Demandes de validation : 10 minutes, une seule en attente (la nouvelle
  remplace l'ancienne), 5 par heure maximum — pour qu'un inconnu ne puisse pas
  harceler le titulaire de demandes jusqu'à ce qu'il accepte par erreur.
  Seul l'appareil qui a fait la demande peut en consulter l'état (même secret).

**Limites connues.**
- Désinstaller l'appli (ou réinitialiser le téléphone) efface la clé : la
  réinstallation compte comme un nouvel appareil. Comme l'ancienne
  installation n'existe plus pour donner son accord, il faut un **code admin**.
- Le maillon faible devient l'admin : quelqu'un peut l'appeler en se faisant
  passer pour un utilisateur. Règle à appliquer : **ne donner un code qu'en
  personne, ou en rappelant soi-même le numéro du compte**.
- La recherche d'utilisateurs expose toujours les numéros (vie privée), mais
  ce n'est plus suffisant pour prendre un compte.

**Fichiers.**

| Fichier | Rôle |
|---------|------|
| [services/devices.py](../services/devices.py) | Secrets, codes, remplacement d'appareil, révocation |
| [blueprints/devices.py](../blueprints/devices.py) | Routes `/api/auth/device/...` (activation, demandes, approbation) |
| [blueprints/auth.py](../blueprints/auth.py) | `register` enregistre l'appareil, `login` exige le secret |
| [blueprints/admin.py](../blueprints/admin.py) | Code d'activation, liste et révocation des appareils |
| [core/security.py](../core/security.py) | Token lié à l'appareil, contrôle à chaque requête |
| [core/http.py](../core/http.py) | Format d'erreur commun `{"error", "code"}` |
| [core/realtime.py](../core/realtime.py) | `disconnect_device` (room `device_<id>`) |
| [core/notifications.py](../core/notifications.py) | Push « Nouvelle connexion » |
| [database/migrations/002_user_devices.sql](../database/migrations/002_user_devices.sql) | Tables `user_devices`, `device_activation_codes`, `device_approval_requests` (aussi dans `schema.sql`) |
| [scripts/create_admin.py](../scripts/create_admin.py) | Affiche désormais un code d'activation (24 h) pour le premier appareil de l'admin, et la base visée |
| `descriptions/devices/*.yml`, `descriptions/admin/*.yml` | Documentation Swagger des nouvelles routes |

**Nouvelles routes.**

| Méthode | Route | Appelée par |
|---------|-------|-------------|
| POST | `/api/auth/device/activate` | nouvel appareil (code admin) |
| POST | `/api/auth/device/requests` | nouvel appareil (demande d'accord) |
| POST | `/api/auth/device/requests/<id>/status` | nouvel appareil (attente) |
| GET | `/api/auth/device/requests/pending` | appareil actif |
| POST | `/api/auth/device/requests/<id>/approve` | appareil actif |
| POST | `/api/auth/device/requests/<id>/reject` | appareil actif |
| GET | `/api/auth/device` | appareil actif (infos) |
| POST | `/api/admin/users/<id>/activation-code` | admin |
| GET | `/api/admin/users/<id>/devices` | admin |
| POST | `/api/admin/users/<id>/devices/revoke` | admin |
| POST | `/api/auth/device/web/requests` | PC d'un admin (demande d'accès, 1 h) |
| POST | `/api/auth/device/web/activate` | PC d'un admin (code de secours) |
| GET | `/api/auth/device/web` | téléphone de l'admin (PC connecté ?) |
| POST | `/api/auth/device/web/revoke` | téléphone ou PC (fermer la session PC) |
| POST | `/api/auth/device/web/extend` | PC (demander +1 h, à confirmer sur le téléphone) |

Nouvel événement Socket.IO : `device_approval_requested` (vers l'appareil actif).
Toutes les erreurs d'authentification portent désormais un `code`
(`TOKEN_MISSING`, `TOKEN_INVALID`, `ACCOUNT_NOT_FOUND`, `ACCOUNT_SUSPENDED`,
`DEVICE_REVOKED`, `DEVICE_NOT_RECOGNIZED`, `INVALID_ACTIVATION_CODE`…).

**Tests.** 16 tests unitaires (dont 3 nouveaux), et un scénario réel sur
conteneurs PostgreSQL/MongoDB jetables : **42/42 vérifications réussies**
(premier admin via le script, inscription, connexion refusée sur un autre
appareil, validation par l'ancien appareil avec notification temps réel,
refus, remplacement de demande, limite 5/heure, code admin, 5 essais, même
code utilisé en parallèle par 2 appareils → un seul succès, révocation,
désactivation des jetons push, messagerie temps réel avec les nouveaux tokens).
Logs serveur : aucune erreur.

> ⚠️ **Changement cassant pour l'appli mobile** : `register` et `login`
> exigent `device_secret`, et les anciens tokens (sans appareil) sont refusés.
> L'application doit être mise à jour en même temps que le backend
> (sans impact sur des utilisateurs : rien n'est encore déployé).

### Accès administration depuis un PC (11/11/2026)

**Besoin.** L'admin doit pouvoir administrer depuis son PC, sans que cela
déconnecte son téléphone, et le PC ne doit servir **qu'à l'administration**.

**Règles.**

| Règle | Détail |
|-------|--------|
| Réservé aux admins | Un utilisateur normal ne peut pas ouvrir de session PC (`403 WEB_ADMIN_ONLY`), même avec un code. |
| Un seul PC par admin | Un nouveau PC remplace l'ancien (déconnecté immédiatement). |
| Le téléphone n'est pas touché | Activer le PC ne révoque que l'ancien PC ; changer de téléphone ne coupe pas le PC. |
| Pas de code à la 1re connexion | Le **téléphone de l'admin valide** chaque connexion du PC (notification « Autoriser l'accès administration sur Chrome - Windows (1 h) ? »). Code admin seulement en secours (`/web/activate`). |
| Session de **1 h** | Limite appliquée **par le serveur** (colonne `expires_at`), pas seulement par le token : passé 1 h, `401 DEVICE_REVOKED` et il faut une nouvelle validation depuis le téléphone. `/login` ne fonctionne jamais pour un PC. |
| Administration uniquement | Depuis un PC, seules `/api/admin/...`, `/api/auth/invite-codes...`, `GET /api/users/me`, `GET /api/auth/verify`, `GET /api/auth/device`, `POST /api/auth/logout` et `POST /api/auth/device/web/revoke` répondent. Tout le reste : `403 WEB_ADMIN_ONLY`. Socket.IO (chat) refusé. |
| Validation = téléphone seulement | Un PC ne peut pas approuver de demande (`403`) : impossible d'enchaîner des sessions PC sans le téléphone. |
| Contrôle à distance | Depuis le téléphone : `GET /api/auth/device/web` (PC connecté ?) et `POST /api/auth/device/web/revoke` (« Déconnecter le PC »). |
| Perte des droits | Admin rétrogradé → session PC révoquée immédiatement (et refusée par le contrôle de session dans tous les cas). |

**Fin de session, prolongation et inactivité.**

| Situation | Comportement |
|-----------|--------------|
| 1 min avant la fin de l'heure | L'interface web affiche « Vous serez déconnecté dans 1 min. Garder la session ? » (elle connaît `session_expires_at`). |
| L'admin clique « Oui » | Le PC appelle `POST /api/auth/device/web/extend` ; le téléphone reçoit « Prolonger d'1 h la session administration sur Chrome - Windows ? ». |
| Confirmé sur le téléphone avant l'échéance | Session prolongée d'1 h à partir de la confirmation ; le PC récupère un **nouveau token** via `/requests/<id>/status`. Même appareil, pas de nouvelle activation. |
| Pas de confirmation (ou refus) avant l'échéance | La session se ferme à l'heure prévue : `401 SESSION_EXPIRED`. Une confirmation trop tardive est refusée (`409`). |
| Aucune requête du PC pendant **5 min** | Session fermée : `401 SESSION_IDLE`. Chaque requête du PC remet le compteur à zéro. |

- La demande de prolongation expire au plus tard avec la session (impossible
  de prolonger une session déjà terminée).
- Les prolongations **ne comptent pas** dans la limite de 5 demandes de
  connexion par heure (elles viennent d'un PC déjà connecté).
- Le téléphone n'a ni durée de session ni délai d'inactivité.

> **Pourquoi l'alerte « 1 min » est affichée par l'interface web et non
> envoyée par le serveur ?** L'interface connaît l'heure exacte de fin
> (`session_expires_at`) dès la connexion : un simple minuteur suffit, sans
> connexion temps réel ouverte côté PC (le PC n'a volontairement pas accès à
> Socket.IO). Elle peut afficher un bandeau dans la page et, si l'onglet est en
> arrière-plan, une notification du navigateur (API `Notification`). Le serveur,
> lui, **applique** les échéances quoi qu'il arrive.

> **Inactivité, côté interface.** Le serveur compte comme activité toute
> requête du PC. L'interface ne doit donc **pas** faire d'appels automatiques
> en arrière-plan (rafraîchissement périodique) sans action de l'admin, sinon
> la session ne serait jamais « inactive ». L'interrogation de
> `/requests/<id>/status` ne compte pas (elle n'utilise pas le token).
> Recommandé en plus : détecter l'inactivité clavier/souris dans la page et
> appeler `POST /api/auth/device/web/revoke` au bout de 5 min.

**CORS.** Avant, n'importe quel site pouvait appeler l'API depuis un
navigateur (`*`). Désormais, seuls les sites listés dans `CORS_ORIGINS` sont
autorisés (exemple : `https://admin.gbekoun.example`, à remplacer par le vrai
domaine). En développement, sans valeur : `localhost:3000`, `5173`, `8081`.
L'application mobile n'est pas concernée (CORS ne s'applique qu'aux navigateurs).

**Recommandations pour l'interface web admin** (à développer) :
- générer un `device_secret` par session de navigateur et le garder en
  `sessionStorage` ou en mémoire, **pas** en `localStorage` ;
- garder le token en mémoire ; à `401 DEVICE_REVOKED`, renvoyer vers l'écran
  « Validez sur votre téléphone » ;
- afficher le compte à rebours de `session_expires_at`.

**Note.** La limite de 5 demandes de connexion par heure (téléphone et PC
confondus) protège le titulaire contre une avalanche de fausses demandes. En
contrepartie, un inconnu qui connaît le numéro de l'admin peut envoyer 5
fausses demandes et empêcher l'admin d'ouvrir une **nouvelle** session PC
pendant 1 h (sans jamais obtenir d'accès : l'admin refuse simplement ces
demandes). Une session PC déjà ouverte peut toujours être prolongée, et le
code de secours reste utilisable.

**Fichiers modifiés** : `services/devices.py` (types `mobile`/`web`, session 1 h),
`core/security.py` (restriction des routes, expiration serveur, admin requis),
`blueprints/devices.py` (routes `/web/...`, validation réservée au téléphone),
`blueprints/auth.py` (`/login` réservé au téléphone), `blueprints/admin.py`
(rétrogradation), `sockets/events.py` (PC refusé), `core/config.py` + `app.py`
(CORS), `core/notifications.py`, schéma + migration 002 (colonnes `kind`,
`expires_at`, index unique par type).

**Tests.** 18 tests unitaires (dont 2 nouveaux) + scénario réel sur
conteneurs jetables : **44/44 vérifications réussies** (validation du PC par
le téléphone sans code, téléphone toujours connecté, session et token de 1 h,
routes d'admin OK et le reste refusé, chat refusé, un PC ne peut pas valider,
un nouveau PC remplace l'ancien, déconnexion du PC depuis le téléphone ou
depuis le PC, expiration côté serveur, utilisateur normal refusé, code de
secours, admin rétrogradé, changement de téléphone sans couper le PC, CORS).

Prolongation et inactivité : 20 tests unitaires (dont 3 nouveaux) + scénario
réel **27/27** (prolongation confirmée → +1 h et nouveau token ; non confirmée
→ fermeture à l'échéance ; confirmation tardive refusée ; refus ; 4 min 50
d'inactivité → toujours connecté, 5 min → `SESSION_IDLE` ; prolongations hors
quota ; téléphone jamais soumis à l'inactivité).

---

## Suite des corrections (11/11/2026)

### R1. La recherche d'utilisateurs exposait les numéros de tout le monde

**Problème.** `GET /api/users/search?q=91` cherchait aussi dans les numéros
(`ILIKE '%91%'`) et renvoyait le numéro complet de chaque résultat : en
quelques requêtes, on pouvait lister tous les comptes et leurs numéros.
`GET /api/users/<id>` renvoyait aussi le numéro de n'importe qui.

**Correction.**

| Recherche | Résultat |
|-----------|----------|
| Par nom ou pseudo (« Kof ») | Profils **sans** `phone_number`, avec `phone_masked` (`+229••••••01`) pour distinguer deux homonymes. |
| Par numéro complet (« +22991000001 », avec ou sans `+`, espaces acceptés) | Correspondance exacte, `phone_number` renvoyé (celui qui cherche le connaît déjà). |
| Par morceau de numéro (« 9100 », « +229 ») | Aucun résultat. |

- Un compte inscrit sans nom a son numéro comme nom et pseudo par défaut :
  ces noms « numériques » ne sont **pas cherchables** (sinon « 9100 »
  permettait de reconstituer un numéro chiffre par chiffre) et sont masqués
  dans les réponses.
- `%` et `_` ne sont plus des jokers SQL ; `limit` plafonné à 50.
- Les utilisateurs bloqués **dans un sens ou dans l'autre** n'apparaissent plus.
- **Profil** (`GET /api/users/<id>`) : `phone_number` visible seulement pour
  soi-même et pour les personnes avec qui on partage une conversation
  (sinon `null` + `phone_masked`). Les réglages de confidentialité
  « contacts » (dernière connexion, photo, **bio** — `privacy_about`
  n'était pas appliqué jusqu'ici) utilisent la même définition. Id invalide → 404.
- `GET /api/users/by-phone/<numéro>` : numéro complet obligatoire (`400 INVALID_PHONE` sinon).
- Pour démarrer une conversation depuis une recherche par nom (sans numéro) :
  `POST /api/conversations/direct` et `POST /<id>/participants` acceptent
  **`user_id`** (en plus de `phone_number`) ; `POST /api/conversations/group`
  accepte **`participant_ids`** (en plus de `participants`).

**Fichiers** : [services/users.py](../services/users.py) (masquage, contacts,
blocages, désignation par id ou numéro), [blueprints/users.py](../blueprints/users.py),
[blueprints/conversations.py](../blueprints/conversations.py), Swagger.

**Tests** : scénario réel **27/27** (dont la fuite par nom par défaut, trouvée
et corrigée pendant le test). 

**Limite** : `by-phone` et la recherche par numéro complet permettent toujours
de vérifier, numéro par numéro, si quelqu'un est inscrit (c'est le principe
« trouver un contact par son numéro »). Seule une limitation du nombre de
requêtes (rate limiting) empêcherait de tester des milliers de numéros.

### R2. `/api/echo` et `/api/health` en disaient trop

**Problème.**
- `POST /api/echo` (public) renvoyait **tous les en-têtes reçus**, dont `Authorization`.
- `GET /api/health` (public) renvoyait le texte des erreurs de base de données
  (qui peut contenir l'hôte, l'utilisateur…) et l'occupation du disque.
- `GET /api/version` affichait la version de Python.
- `GET /api/admin/stats/messages` renvoyait le texte brut des exceptions.

**Correction.**
- `/api/echo` : **404 en production** (disponible seulement avec `FLASK_DEBUG=1`,
  et sans `Authorization` ni `Cookie` dans la réponse).
- `/api/health` : seulement `up` / `down` par base, **code 503** si une base
  est injoignable (exploitable par un moniteur). Le détail va dans les logs.
- `/api/version` : sans version de Python ni fausse date de build.
- Plus aucun texte d'exception dans les réponses : les erreurs 404 et 500
  renvoient désormais du JSON générique (`NOT_FOUND`, `INTERNAL_ERROR`).

**Trouvé en testant — MongoDB qui gèle bloquait le serveur.** Par défaut,
pymongo attend 30 s pour trouver le serveur et **indéfiniment** la réponse
d'une opération. Si MongoDB gèle (coupure réseau), chaque requête bloque un
thread du serveur pour toujours. Désormais : 5 s pour trouver le serveur, 5 s
pour se connecter, 15 s maximum par opération ; `/api/health` utilise une
connexion dédiée limitée à 3 s (vérifié : réponse `503` en 3,0 s avec
MongoDB gelé, au lieu de ne jamais répondre).

### R3. Les actions faites par REST n'étaient pas diffusées en temps réel

**Problème.** Un message envoyé par la route REST (secours quand le socket
est coupé) n'arrivait chez les autres qu'au rechargement de la conversation.
Idem pour la modification d'un message, les réactions et l'accusé de lecture
faits par REST.

**Correction.** Ces routes diffusent maintenant le même événement que leur
équivalent Socket.IO :

| Route REST | Événement diffusé | À qui |
|------------|-------------------|-------|
| `POST /api/messages/<conv>/messages` | `new_message` | autres participants |
| `PUT /api/messages/<conv>/messages/<id>` | `message_edited` | toute la conversation |
| `POST /api/messages/<conv>/messages/<id>/react` | `reaction_added` | toute la conversation |
| `DELETE /api/messages/<conv>/messages/<id>/react` | `reaction_removed` | toute la conversation |
| `POST /api/conversations/<conv>/read` | `message_read` | autres participants |
| `POST /api/messages/<conv>/messages/<id>/forward` | `new_message` | autres participants de la conversation cible |

L'auteur ne reçoit pas `new_message` / `message_read` pour sa propre action
(il a déjà la réponse HTTP) : pas de doublon dans l'appli, même comportement
que par socket. `POST /read` sur une conversation dont on n'est pas membre → 404.

**Fichiers** : [core/realtime.py](../core/realtime.py) (`emit_to_conversation`),
[blueprints/messages.py](../blueprints/messages.py), [blueprints/conversations.py](../blueprints/conversations.py),
[blueprints/utils.py](../blueprints/utils.py), [core/database.py](../core/database.py), [app.py](../app.py).

**Tests R2 + R3** : 22 tests unitaires (dont 2 nouveaux) + scénario réel **18/18**.

### R4. Photo de profil (upload)

**Problème.** Aucune route d'upload : la photo de profil ne pouvait pas être
ajoutée depuis l'appli (cf. PDF d'observations). `PUT /api/users/me`
acceptait n'importe quelle URL dans `avatar_url` (un lien externe pouvait
servir à savoir qui consulte un profil).

**Stockage.** Sur le disque du serveur, dans `UPLOAD_FOLDER` (volume Docker
persistant `uploads_data`), puisque le déploiement se fait désormais sur une
machine perso (plus de Render, dont le disque était effacé à chaque déploiement).

**Fonctionnement.**

| Route | Rôle |
|-------|------|
| `POST /api/users/me/avatar` (multipart, champ `file`) | Envoie une photo ; renvoie `avatar_url` |
| `DELETE /api/users/me/avatar` | Retire la photo |
| `GET /api/media/avatars/<user_id>/<fichier>.jpg` | Affiche la photo (token requis) |

- Formats acceptés : JPEG, PNG, WebP, GIF ; **2 Mo maximum**.
- Chaque image est **vérifiée** (vraie image, protection contre les « bombes
  de décompression »), **recadrée au carré, réduite à 512×512** et
  **réencodée en JPEG** : les métadonnées EXIF, dont la **position GPS** que
  contiennent souvent les photos de téléphone, sont supprimées, ainsi que
  tout contenu caché dans le fichier.
- Nom de fichier aléatoire, nouveau à chaque changement (pas d'image
  périmée en cache côté appli) ; l'ancienne photo est effacée du disque.
- **Confidentialité** : l'affichage respecte le réglage photo
  (`everyone` / `contacts` / `nobody`) et les blocages ; seule la photo
  actuelle est servie. Le PC d'un admin peut afficher les photos.
- Suppression du compte (par l'utilisateur ou par un admin) → photo effacée.
- `PUT /api/users/me` refuse désormais `avatar_url` (`400 AVATAR_USE_UPLOAD`).

**Côté appli mobile** : `avatar_url` est un chemin relatif à préfixer par
l'adresse de l'API, et l'image se charge **avec l'en-tête Authorization**
(ex. expo-image : `source={{ uri: API_URL + avatar_url, headers: { Authorization: 'Bearer ' + token } }}`).

**Fichiers** : [services/media.py](../services/media.py), [blueprints/media.py](../blueprints/media.py),
[blueprints/users.py](../blueprints/users.py), [blueprints/admin.py](../blueprints/admin.py),
`requirements.txt` (ajout de **Pillow**), Swagger.

**Tests** : 27 tests unitaires (dont 5 sur le traitement d'image : recadrage,
suppression GPS vérifiée, PNG transparent, fichier piégé, 2 Mo, `../`) +
scénario réel **30/30** (envoi, affichage, 3 réglages de confidentialité,
remplacement, refus, chemin piégé, blocage, suppression du compte).

### R5. Docker : un fichier pour le développement, un pour la production

**Contexte.** Le déploiement se fait désormais sur un serveur perso, tout en
Docker avec volumes persistants (Render abandonné).

**`docker-compose.dev.yml`** (remplace `docker-compose.yml`) : code monté dans
le conteneur avec **rechargement automatique**, Swagger ouvert, bases
accessibles depuis la machine (5435, 27020).

**`docker-compose.prod.yml`** :

| Élément | Détail |
|---------|--------|
| **Nginx** (seul exposé) | HTTPS (TLS 1.2/1.3), redirection HTTP → HTTPS, WebSocket pour Socket.IO, HSTS + en-têtes de sécurité, version masquée, envois limités à 10 Mo, Swagger fermé, connexions par IP ou faux nom de domaine coupées |
| **Limitation de débit** | `/api/auth/` 30/min (rafale 20), reste de l'API 20/s (rafale 60), 50 connexions simultanées par IP — protège notamment contre l'essai en masse de numéros ou de codes |
| **Let's Encrypt** | `deploy/init-letsencrypt.sh` (une fois), puis renouvellement automatique (certbot toutes les 12 h, rechargement nginx toutes les 6 h) |
| **Bases** | Non exposées sur Internet ; volumes `postgres_data`, `mongo_data`, `uploads_data` |
| **Sauvegardes** | Service `backup` : chaque jour à 3 h, PostgreSQL + MongoDB + photos, intégrité SHA-256, conservation 14 jours, `restore.sh` (avec confirmation `--yes`) |
| **Robustesse** | Redémarrage automatique, healthchecks, démarrage dans l'ordre (bases → API → nginx), rotation des logs (10 Mo × 5) |
| **API** | `TRUST_PROXY=1` : vraie IP du client, `https`/`wss` correctement annoncés (`ProxyFix`) |

**Autres changements.** `pymongo` 4.5 → **4.10.1** (compatible MongoDB 8.0,
version par défaut des deux fichiers ; MongoDB 6.0 n'est plus maintenu depuis
juillet 2025). PostgreSQL 16 par défaut. Versions configurables
(`POSTGRES_IMAGE`, `MONGO_IMAGE`).

**Trouvé en testant.**
- Le site d'exemple de l'image nginx (« Welcome to nginx ») répondait à
  toute requête par IP : neutralisé.
- Docker Compose interprète les `$` du `.env` : un mot de passe contenant `$`
  est silencieusement tronqué. Règle documentée : lettres, chiffres, `-`, `_`.
- Mettre à jour MongoDB en envoyant `init.js` par l'entrée standard ne
  l'exécutait pas : la commande documentée utilise le fichier monté.

**Tests** : images construites (API avec Pillow et pymongo 4.10.1 ; backup avec
`mongodb-tools`) ; pile de production lancée en local avec un certificat
autosigné (`postgres:15-alpine`, `mongo:6.0`) : **25/26** vérifications — la
seule « erreur » venait du script de test (rafale déjà entamée par des appels
précédents), le quota fonctionnant comme prévu. Vérifié : redirection HTTPS,
en-têtes, Swagger fermé, `wss://` annoncé, TLS refusé pour un autre domaine,
**Socket.IO en vrai WebSocket via nginx**, message REST diffusé, photo
envoyée/servie, 413 au-delà de 10 Mo, 429 sur rafale de connexions,
sauvegarde complète puis **restauration** (utilisateur, messages et photo
supprimés → de retour).

**Non testé ici** (nécessite le vrai serveur) : l'obtention du certificat
Let's Encrypt et son renouvellement (domaine public requis), MongoDB 8.0 et
PostgreSQL 16 (testés en 6.0 et 15). À faire au premier déploiement :
`LETSENCRYPT_STAGING=1 ./deploy/init-letsencrypt.sh` pour un essai sans quota.

### R6. Texte uniquement pour l'instant (pas de médias)

**Décision** : pas d'upload de médias dans le chat pour le moment (peut-être
plus tard). En conséquence, les types `image`, `video`, `audio` et `file`
sont **refusés à l'envoi** (`400`, code `MEDIA_NOT_SUPPORTED`) : sans upload,
un client pouvait envoyer un « média » pointant vers n'importe quelle URL
externe (traçage de ceux qui l'affichent). Types acceptés : `text`,
`location`, `contact`. `/api/config` annonce désormais `file_sharing` et
`voice_messages` à `false`. La photo de profil (R4) n'est pas concernée.

### R7. Un groupe pouvait se retrouver sans administrateur

**Problème.** « Supprimer la discussion » sur un groupe le faisait quitter
sans aucun contrôle : le dernier admin pouvait partir et laisser le groupe
sans admin (plus personne pour ajouter ou retirer des membres). Même chose
lors de la suppression d'un compte (par l'utilisateur ou par un admin) ; le
compte supprimé restait d'ailleurs membre de ses conversations.

**Correction** ([services/groups.py](../services/groups.py)) : règle unique
appliquée à **tous** les départs — quitter, supprimer la discussion, être
retiré, suppression du compte :

- si le dernier admin part, **le membre le plus ancien devient admin**
  automatiquement (au lieu de refuser le départ : on ne peut pas refuser la
  suppression d'un compte) ;
- verrou par conversation : deux admins qui partent en même temps ne
  laissent pas le groupe sans admin (vérifié) ;
- événements temps réel `participant_left` et `admin_changed`
  (`reason: "last_admin_left"`) envoyés aux membres ;
- la réponse de `leave` / `DELETE` / retrait contient `promoted_user_id` ;
- un compte supprimé quitte toutes ses conversations et n'est plus
  « réactivé » dans une discussion directe quand l'autre lui écrit.

**Trouvé en testant** : les membres d'un groupe créé en une fois avaient tous
exactement la même date d'arrivée (heure de la transaction) : « le plus
ancien » était tiré au hasard. Désormais chaque membre a une date distincte,
dans l'ordre où l'appli les a listés à la création ; un membre réintégré
repart de la date de sa réintégration.

### R8. Nom de l'expéditeur dans les messages

**Problème.** Les messages ne contenaient que `sender_id` : dans un groupe,
l'appli ne savait pas afficher qui a écrit quoi (cf. PDF d'observations).

**Correction.** Chaque message renvoyé contient
`sender: {id, display_name, avatar_url}` : historique, message seul,
recherche, épinglés, mentions, dernier message de la liste des
conversations, événements `new_message` (socket, REST, transfert). Les noms
sont lus **en une seule requête** par liste et **à l'affichage** (un
changement de nom se voit aussi sur les anciens messages). Compte supprimé :
`display_name = "Compte supprimé"`. (`avatar_url` reste soumis à la
confidentialité de la photo lors de son affichage.)

### R9. Mise à jour de Flask-SocketIO

`flask-socketio` 5.3.4 → **5.5.1**, `python-socketio` 5.9.0 → **5.12.1**
(`python-engineio` 4.13.2 épinglé). Le client de test Socket.IO reçoit
désormais les événements (incompatibilité de la 5.3.4). Aucun changement de
code nécessaire.

**Tests R6 à R9** : 30 tests unitaires (dont 2 nouveaux) ; scénario réel
**16/16** (succession dans les 4 cas, départ simultané de 2 admins, nom de
l'expéditeur partout, compte supprimé, refus des médias, Socket.IO 5.5 en
WebSocket et en long-polling) ; image de production reconstruite avec les
nouvelles versions et scénario complet à travers nginx rejoué (même
résultat qu'avant, aucune erreur dans les logs Gunicorn). Note : avec le
serveur de **développement** (`python app.py`), la fermeture d'une connexion
WebSocket affiche `write() before start_response` dans les logs — message
connu et sans conséquence, absent avec Gunicorn.

### R10. Avatar de groupe : plus d'URL externe

**Problème.** `PUT /api/conversations/<id>` (et la création de groupe)
acceptaient n'importe quelle URL comme avatar de groupe : un lien externe
permettait de savoir qui affiche le groupe (même problème que l'ancien avatar
de profil, corrigé en R4).

**Correction.**
- `POST /api/conversations/<id>/avatar` (multipart, champ `file`) et
  `DELETE …/avatar`, réservés aux **admins du groupe** ; mêmes règles que la
  photo de profil (2 Mo, 512×512, réencodage JPEG, EXIF/GPS supprimés).
- Affichage `GET /api/media/groups/<conversation_id>/<fichier>.jpg` :
  **membres actifs du groupe uniquement**, seule l'image actuelle est servie.
- `avatar_url` refusé dans `PUT /<id>` et à la création (`400 AVATAR_USE_UPLOAD`).
- Événement `conversation_updated` (`changes.avatar_url` ou `changes.name`).
- Le schéma efface les anciens avatars (profil et groupe) qui pointaient vers
  des URL externes (`schema.sql`, rejouable).
- `services/media.py` est devenu générique (profils et groupes).

### R11. L'API utilisait les comptes administrateurs des bases

**Problème.** L'API se connectait à PostgreSQL avec le propriétaire de la base
et à MongoDB avec le compte root : une faille dans l'API (injection, fuite du
`.env` du conteneur…) donnait tous les droits (supprimer les tables, lire
les autres bases, créer des comptes).

**Correction.** Deux comptes dédiés, utilisés **uniquement** par l'API :

| Compte | Autorisé | Refusé (vérifié) |
|--------|----------|------------------|
| PostgreSQL `gbekoun_app` ([database/postgres/app_user.sql](../database/postgres/app_user.sql)) | `SELECT`, `INSERT`, `UPDATE`, `DELETE` sur les tables du schéma `gbekoun` (y compris les futures) | `DROP TABLE`, `ALTER TABLE`, `CREATE TABLE`, `TRUNCATE`, `CREATE ROLE`, devenir superutilisateur, lire `pg_authid` |
| MongoDB `gbekoun_app`, rôle sur mesure `gbekounApp` ([database/mongo/init.js](../database/mongo/init.js)) | lire/ajouter/modifier/supprimer des documents de `messages` | supprimer la collection, créer une collection, écrire ailleurs, supprimer un index, modifier le validateur, `dropDatabase`, lire une autre base, créer un utilisateur |

- Création **automatique au premier démarrage** (`02_app_user.sh`, `init.js`) ;
  pour une base existante : `deploy/db/create-app-users.sh` (rejouable).
- Les comptes administrateurs restent réservés au schéma, aux migrations, aux
  sauvegardes et aux restaurations (service `backup`).
- Nouvelles variables : `PG_APP_USER`, `PG_APP_PASSWORD`, `MONGO_APP_USER`,
  `MONGO_APP_PASSWORD` (obligatoires dans les deux fichiers compose).

**Trouvé en testant.**
- Le rôle standard MongoDB `readWrite` permet de **supprimer la collection
  entière des messages** : vérifié à mes dépens, la commande de test l'a
  réellement supprimée dans la pile de test (réparée ensuite). D'où le rôle
  sur mesure, limité à la collection `messages`.
- Un fichier monté seul dans un conteneur garde son **ancienne version**
  après modification ou `git pull` : le script et la commande documentée
  envoient désormais les scripts depuis le dépôt.

**Tests R10 + R11** : base existante mise à niveau avec le script (deux
passages), droits vérifiés un par un (tableau ci-dessus), scénario complet
de production rejoué avec les comptes limités (y compris sauvegarde et
**restauration**) + avatar de groupe **9/9** ; installation neuve : comptes
créés automatiquement et API opérationnelle.

### R12. Un seul format de date dans toute l'API

**Problème** (cf. PDF d'observations sur les dates). Trois formats coexistaient :
messages en ISO 8601 (`2026-11-11T09:30:00.000Z`), dates PostgreSQL au format
HTTP de Flask (`Wed, 11 Nov 2026 09:30:00 GMT` : `created_at`, `last_seen`,
`muted_until`, `joined_at`…), et quelques dates fabriquées à la main sans
fuseau (`2026-11-11T09:30:00.123456` : `/api/time`, `/api/health`, statut…).

**Correction.** Fournisseur JSON global (`ISODateJSONProvider` dans `app.py`) :
**toutes** les dates sortent en ISO 8601 UTC à la milliseconde, terminées par
`Z` (les dates PostgreSQL sans fuseau sont en UTC). Les dates « à la main »
passent désormais par ce fournisseur. Test unitaire ajouté.

> Changement visible par l'appli mobile : à intégrer en même temps que les
> autres (voir docs/API_MOBILE.md, changement n° 9).

### Vérification complète du projet (11/11/2026, avant la documentation)

Installation **neuve** de la pile de production (nginx + API + PostgreSQL +
MongoDB + sauvegardes, comptes à droits limités), testée de bout en bout à
travers nginx en HTTPS :

| Section | Résultat |
|---------|----------|
| Infrastructure (nginx, santé, utilitaires) | 16/16 |
| Comptes, appareils, codes d'invitation | 25/25 |
| Session PC de l'admin | 9/9 |
| Utilisateurs, recherche, confidentialité, photo | 18/18 |
| Conversations directes et groupes | 26/26 |
| Messages (REST + tous les événements Socket.IO) | 25/25 |
| Statuts et présence | 5/5 (\*) |
| Départs et succession des admins | 4/4 |
| Administration | 16/16 |
| Fin de session, suppression de compte | 3/3 |
| Sauvegarde et restauration | 2/2 |
| **Couverture des routes** | **107/107 routes appelées** |

(\*) la présence n'est envoyée qu'aux contacts existant au moment de la
connexion : vérifiée par un test dédié (contact vu en ligne puis hors ligne).

Contrôles complémentaires : 30 tests unitaires, compilation, analyse statique
(pyflakes), 98 fiches Swagger valides et toutes utilisées, `nginx -t`,
fichiers compose dev/prod valides ; **aucune erreur** dans les logs de l'API,
de PostgreSQL, de MongoDB et du service de sauvegarde (aucun refus de droit
avec les comptes limités).

Corrigé pendant cette vérification : `/oauth2-redirect.html` (page de
Swagger) n'était pas fermé en production ; les dépassements de quota nginx
sont journalisés en avertissement (et non en erreur) ; une requête SQL
inutile exécutée à chaque `POST /api/status/online` et un import inutile
retirés.

---

## Réorganisation du projet

Avant, `app.py` faisait tout (config, pool PostgreSQL, Mongo, JWT, push,
Socket.IO…) et chaque blueprint faisait `from app import ...`, ce qui créait
des imports circulaires. La logique d'envoi de message était copiée-collée
entre la route REST et le socket.

```
gbekoun_backend/
├── app.py                  # Point d'entrée : crée l'app, enregistre les routes et les sockets
├── core/                   # Briques techniques partagées
│   ├── config.py           #   Variables d'environnement + validation au démarrage
│   ├── database.py         #   Pool PostgreSQL (transaction, execute_query) + MongoDB
│   ├── security.py         #   JWT, require_auth, require_admin
│   ├── extensions.py       #   Instance Socket.IO
│   ├── realtime.py         #   Rooms Socket.IO pilotées depuis le REST (join/leave/disconnect)
│   └── notifications.py    #   Notifications push Expo
├── services/
│   └── messaging.py        # Envoi de message (commun REST + socket)
├── blueprints/             # Routes REST (auth, conversations, messages, users, status, admin, utils)
├── sockets/events.py       # TOUS les événements Socket.IO
├── utils/                  # expo_push.py, message_helpers.py
├── descriptions/           # Documentation Swagger (YAML) des routes
├── database/
│   ├── postgres/schema.sql #   Schéma complet, idempotent
│   ├── mongo/init.js       #   Collections, validation, index
│   └── migrations/         #   Migrations incrémentales (001_push_devices.sql)
├── scripts/create_admin.py # Création du premier administrateur
├── tests/e2e/              # Tests (sans base de données)
├── docs/                   # CORRECTIONS.md (ce fichier), DEMARRAGE.md
├── deploy/                 # Production : nginx, sauvegardes, certificat HTTPS (voir R5)
├── Dockerfile              # Image de l'API
├── docker-compose.dev.yml  # Développement (code monté, rechargement automatique)
└── docker-compose.prod.yml # Production (nginx, certbot, api, bases, sauvegardes)
```

Les URL de l'API, les noms d'événements Socket.IO et les formats de réponse
**n'ont pas changé** (sauf ce qui est listé dans
[Changements visibles](#changements-visibles-par-lapplication-mobile)).
La commande de démarrage en production (`gunicorn ... app:app`) est identique.

---

## Fichiers supprimés

**Code mort (jamais importé, et souvent cassé)** — supprimés de Git :

| Fichier | Raison |
|---------|--------|
| `config.py` | Doublon de la config, jamais utilisé (`from app.config` n'existait pas). Remplacé par `core/config.py`. |
| `db/mongo_conn.py`, `db/pg_conn.py` | Jamais importés, imports cassés (`app.config`). |
| `middleware/auth.py` | Jamais importé, import cassé. Remplacé par `core/security.py`. |
| `utils/jwt-utils.py` | Jamais importé (et non importable : tiret dans le nom). |
| `utils/validators.py`, `workers/tasks.py`, `blueprints/media.py` | Fichiers vides. |
| `entrypoint.sh` | Désactivé dans le Dockerfile, contenait un mot de passe Mongo. |
| `gbekoundb.sql` | Remplacé par `database/postgres/schema.sql` (l'ancien échouait sous Docker : index créés deux fois ; contenait un mot de passe et l'admin `+1234567890`). |
| `gekoun_messages.js` | Remplacé par `database/mongo/init.js` (`use` ne fonctionne pas dans un script Docker ; mot de passe en dur ; index TTL). |
| `migrations/` | Déplacé dans `database/migrations/`. |

**Dépendances retirées de `requirements.txt`** : `eventlet` (non utilisé, le
mode est `threading`), `twilio`, `bcrypt` (jamais importés), `certifi` (n'était
plus utilisé depuis le retrait de `tlsCAFile`). Ajout explicite de
`simple-websocket` (transport WebSocket en mode threading) et `dnspython`
(URI `mongodb+srv://` d'Atlas) qui étaient déjà installés en dépendances indirectes.

**Dossiers vides appartenant à root** : `setup_postgres_messaging.sql/` et
`init-mongo.js/` étaient des **dossiers** (Docker les avait créés en montant
des fichiers inexistants). Supprimés.

**Non supprimés (fichiers personnels, non versionnés)** — à toi de décider :
`note.odt`, `commande.md`, `ls.txt`, `Liste_complete_urls.md`,
`mongo_messages.md`, `Creation de conteneur.txt`, `gbekoundb2.sql`,
`docker-compose-dev.yml`, le PDF d'observations, et **`render_db`** (contient
les identifiants Render en clair : à supprimer après la rotation du mot de passe).
Le dossier `uploads/` appartient à root (créé par Docker) ; il est ignoré par Git.

**Autres fichiers modifiés** : `Dockerfile` (plus de `gcc`/`g++`/`curl` inutiles,
logs non bufferisés), `.dockerignore` (désormais versionné pour que le build
l'utilise ; exclut secrets, tests, docs), `.gitignore` (nettoyé, doublons retirés).

---

## Petites corrections faites au passage

Dans les fichiers déjà modifiés pour les points ci-dessus :

- **Recherche de messages** : le texte cherché est échappé (`re.escape`). Avant,
  taper `(` ou `*` provoquait une erreur 500, et une regex piégée pouvait
  surcharger MongoDB.
- **IDs invalides** (`before_id`, `message_id`) : réponse `400` au lieu de `500`.
- **`limit`** plafonné à 200 sur les routes de messages (valeur annoncée par `/api/config`).
- **Corps JSON absent** : toutes les routes acceptent un corps vide sans
  planter (`request.get_json(silent=True) or {}`).
- **Type de message** validé (`text`, `image`, `video`, `audio`, `file`,
  `location`, `contact`) : avant, un type inconnu était rejeté par MongoDB → 500.
- **Compteur de non-lus** : n'augmente plus pour les membres qui ont quitté la conversation.
- **Suppression REST** : `result.deleted_count` (inexistant sur un `UpdateResult`) retiré.
- **Retrait / départ d'un groupe** : le rôle d'admin du groupe est retiré
  (sinon il revenait en cas de réintégration).
- `print(conversation_id)` de débogage retiré de `get_conversation`.
- Les erreurs du thread de nettoyage des utilisateurs inactifs sont journalisées
  au lieu de tuer le thread silencieusement.

---

## Tests

[tests/e2e/test_message_flow.py](../tests/e2e/test_message_flow.py) : 13 tests,
sans base de données (PostgreSQL et MongoDB simulés en mémoire). En plus des 3
tests existants, ajout de tests pour les points 3, 4, 6, 7, 9, 10 et le
compteur de non-lus.

```bash
FLASK_DEBUG=1 .genv/bin/python tests/e2e/test_message_flow.py -v
```

Le fichier reste ignoré par Git (choix existant dans `.gitignore`).

> Note : avec Flask-SocketIO 5.3.4 + python-socketio 5.9, le client de test
> Socket.IO ne reçoit pas les événements (`get_received()` reste vide —
> incompatibilité connue, corrigée dans Flask-SocketIO 5.3.5+). Les tests
> interceptent donc `emit()`. Cela n'affecte pas le fonctionnement réel.

### Test sur de vraies bases (conteneurs jetables `postgres:15-alpine` + `mongo:6.0`)

- `database/postgres/schema.sql` : appliqué sur une base vide **puis rejoué une
  2e fois** sans erreur (idempotent) → 8 tables créées.
- `database/mongo/init.js` : collection `messages` + validateur + 3 index (pas d'index TTL), rejoué sans erreur.
- `scripts/create_admin.py` : crée l'admin, puis le relancer le « promeut » sans créer de doublon.
- Démarrage en mode production sans `SECRET_KEY`/`JWT_SECRET`/`PG_PASSWORD` → refus avec message clair.
- API réelle (`FLASK_DEBUG=0`) + vrais clients Socket.IO : **29/29 vérifications réussies** :

| Point | Vérification |
|-------|--------------|
| 3 | Transfert depuis une conversation étrangère → 403 ; transfert légitime OK |
| 4 | Suspension : socket fermé immédiatement, REST 403, login 403 ; compte supprimé : 401 / login 404 |
| 6 | Présence en ligne / hors ligne reçue ; membre retiré : ne reçoit plus les messages ni les « typing » |
| 7 | Réaction par socket enregistrée, puis remplacée |
| 8 | 15 erreurs SQL d'affilée (plus que la taille du pool), puis 15 requêtes normales : toutes OK |
| 9 | `GET /invite-codes?include_used=false` → 200 |
| 10 | Envoi dans une conversation en sourdine → 201 |
| 11 | Conversation supprimée → disparaît ; nouveau message → réapparaît avec seulement le nouveau message ; `POST /direct` renvoie la même ; conversation avec soi-même → 400 |
| 12 | Backup sans `mongodump` installé → 501 explicite |
| 13 | 2 inscriptions **simultanées** avec le même code → un seul succès ; numéro déjà pris → 409 sans consommer le code ; groupe avec doublons → OK |
| — | Recherche avec `(` → 200 |

---

## Changements visibles par l'application mobile

| Avant | Maintenant |
|-------|-----------|
| Envoyer dans une conversation en sourdine → `403` / erreur socket `MUTED` | L'envoi fonctionne (seules les notifications reçues sont coupées). |
| Compte suspendu : tout fonctionnait | `403 {"error": "Compte suspendu"}` sur toutes les routes ; socket refusé. |
| Compte supprimé : tout fonctionnait | `401` ; connexion `404`. |
| Supprimer une conversation : les messages restaient | L'historique est effacé pour l'utilisateur ; une discussion directe réapparaît au prochain message. |
| `POST /direct` après suppression : nouvelle conversation | Renvoie la conversation existante (`200`, `existing: true`). |
| Message transféré : pas d'événement temps réel | `new_message` émis dans la conversation cible (l'expéditeur le reçoit aussi sur ses appareils). |
| Socket `react` : ne marchait pas | Fonctionne ; événement `reaction_added`. |
| Socket : erreurs sur ID invalide parfois silencieuses | Événement `error` avec `code` (`INVALID_ID`, `FORBIDDEN`, `NOT_FOUND`…). |
| Présence `user_presence` : jamais envoyée | Envoyée aux contacts à la connexion / déconnexion. |
| Messages : `sender_id` seulement | `sender: {id, display_name, avatar_url}` en plus (R8). |
| Types `image` / `video` / `audio` / `file` acceptés avec n'importe quel contenu | Refusés (`MEDIA_NOT_SUPPORTED`) : texte, position, contact uniquement (R6). |
| Le dernier admin ne pouvait pas quitter (`400`) mais pouvait « supprimer » le groupe | Il peut partir ; le membre le plus ancien devient admin (`promoted_user_id`, événement `admin_changed`) (R7). |
| Dates : format HTTP pour les comptes/conversations, ISO pour les messages | ISO 8601 UTC partout (R12). |
| Recherche par nom : numéros complets renvoyés | `phone_masked` seulement ; conversations démarrables par `user_id` (R1). |
| Message envoyé par REST : pas d'événement temps réel | `new_message` diffusé aux autres participants (R3). |

---

## Reste à faire

Déjà traités : point 1 (connexion liée à l'appareil, accès PC admin) et R1 à
R11.

1. **Médias du chat** (images, vidéos, vocaux, documents) : volontairement
   reportés (R6). À concevoir le jour où ils seront nécessaires.
