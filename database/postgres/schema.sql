-- =============================================================
-- GBEKOUN - Schéma PostgreSQL
--
-- Idempotent : peut être rejoué sans erreur sur une base existante
-- (tout est en IF NOT EXISTS). Exécuté automatiquement par
-- docker-compose au premier démarrage du conteneur PostgreSQL.
--
-- Exécution manuelle :
--   psql "$DATABASE_URL" -f database/postgres/schema.sql
--
-- Aucun mot de passe ni compte par défaut ici : le premier
-- administrateur se crée avec scripts/create_admin.py.
-- Nécessite PostgreSQL 13+ (gen_random_uuid intégré).
-- =============================================================

CREATE SCHEMA IF NOT EXISTS gbekoun;

-- Mise à jour automatique de la colonne updated_at
CREATE OR REPLACE FUNCTION gbekoun.set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- -------------------------------------------------------------
-- users : comptes utilisateurs
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gbekoun.users (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    phone_number          VARCHAR(20)  NOT NULL UNIQUE,
    display_name          VARCHAR(100) NOT NULL,
    username              VARCHAR(50)  UNIQUE,
    bio                   TEXT,
    avatar_url            TEXT,
    is_online             BOOLEAN      DEFAULT FALSE,
    last_seen             TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    is_admin              BOOLEAN      DEFAULT FALSE,
    is_suspended          BOOLEAN      DEFAULT FALSE,
    suspended_at          TIMESTAMP    NULL,
    suspended_reason      TEXT         NULL,
    privacy_last_seen     VARCHAR(20)  DEFAULT 'everyone',
    privacy_profile_photo VARCHAR(20)  DEFAULT 'everyone',
    privacy_about         VARCHAR(20)  DEFAULT 'everyone',
    deleted_at            TIMESTAMP    NULL,
    created_at            TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    updated_at            TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
);

-- Colonnes ajoutées au fil du temps (pour les bases créées avec l'ancien script)
ALTER TABLE gbekoun.users
    ADD COLUMN IF NOT EXISTS is_admin              BOOLEAN     DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS is_suspended          BOOLEAN     DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS suspended_at          TIMESTAMP   NULL,
    ADD COLUMN IF NOT EXISTS suspended_reason      TEXT        NULL,
    ADD COLUMN IF NOT EXISTS privacy_last_seen     VARCHAR(20) DEFAULT 'everyone',
    ADD COLUMN IF NOT EXISTS privacy_profile_photo VARCHAR(20) DEFAULT 'everyone',
    ADD COLUMN IF NOT EXISTS privacy_about         VARCHAR(20) DEFAULT 'everyone',
    ADD COLUMN IF NOT EXISTS deleted_at            TIMESTAMP   NULL;

DROP TRIGGER IF EXISTS trg_users_updated_at ON gbekoun.users;
CREATE TRIGGER trg_users_updated_at
    BEFORE UPDATE ON gbekoun.users
    FOR EACH ROW EXECUTE FUNCTION gbekoun.set_updated_at();

-- -------------------------------------------------------------
-- invite_codes : codes d'invitation générés par un admin
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gbekoun.invite_codes (
    id          SERIAL PRIMARY KEY,
    code        VARCHAR(10) NOT NULL UNIQUE,
    used_by     UUID        NULL REFERENCES gbekoun.users(id) ON DELETE SET NULL,
    used_at     TIMESTAMP   NULL,
    created_by  UUID        NOT NULL REFERENCES gbekoun.users(id),
    created_at  TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
    expires_at  TIMESTAMP   NOT NULL
);

-- -------------------------------------------------------------
-- conversations : discussions directes (1-1) et groupes
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gbekoun.conversations (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type        VARCHAR(10) NOT NULL CHECK (type IN ('direct', 'group')),
    name        VARCHAR(100),
    avatar_url  TEXT,
    created_by  UUID        REFERENCES gbekoun.users(id) ON DELETE SET NULL,
    created_at  TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP   DEFAULT CURRENT_TIMESTAMP
);

DROP TRIGGER IF EXISTS trg_conversations_updated_at ON gbekoun.conversations;
CREATE TRIGGER trg_conversations_updated_at
    BEFORE UPDATE ON gbekoun.conversations
    FOR EACH ROW EXECUTE FUNCTION gbekoun.set_updated_at();

-- -------------------------------------------------------------
-- conversation_participants : membres d'une conversation
-- deleted_at non NULL = a quitté / a été retiré / a supprimé la discussion
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gbekoun.conversation_participants (
    conversation_id      UUID      REFERENCES gbekoun.conversations(id) ON DELETE CASCADE,
    user_id              UUID      REFERENCES gbekoun.users(id) ON DELETE CASCADE,
    joined_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    unread_count         INTEGER   DEFAULT 0,
    last_read_message_id VARCHAR(24),
    is_admin             BOOLEAN   DEFAULT FALSE,
    muted_until          TIMESTAMP NULL,
    is_archived          BOOLEAN   DEFAULT FALSE,
    is_pinned            BOOLEAN   DEFAULT FALSE,
    deleted_at           TIMESTAMP NULL,
    PRIMARY KEY (conversation_id, user_id)
);

ALTER TABLE gbekoun.conversation_participants
    ADD COLUMN IF NOT EXISTS is_archived BOOLEAN   DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS is_pinned   BOOLEAN   DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS deleted_at  TIMESTAMP NULL;

-- -------------------------------------------------------------
-- blocked_users : blocages entre utilisateurs
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gbekoun.blocked_users (
    blocker_id  UUID NOT NULL REFERENCES gbekoun.users(id) ON DELETE CASCADE,
    blocked_id  UUID NOT NULL REFERENCES gbekoun.users(id) ON DELETE CASCADE,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (blocker_id, blocked_id)
);

-- -------------------------------------------------------------
-- starred_messages : messages marqués comme importants
-- (le contenu reste dans MongoDB, on ne garde que l'identifiant)
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gbekoun.starred_messages (
    id               SERIAL PRIMARY KEY,
    user_id          UUID        NOT NULL REFERENCES gbekoun.users(id) ON DELETE CASCADE,
    conversation_id  UUID        NOT NULL REFERENCES gbekoun.conversations(id) ON DELETE CASCADE,
    message_id       VARCHAR(24) NOT NULL,
    starred_at       TIMESTAMP   DEFAULT NOW(),
    UNIQUE (user_id, message_id)
);

-- -------------------------------------------------------------
-- reports : signalements traités par les admins
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gbekoun.reports (
    id               SERIAL PRIMARY KEY,
    reporter_id      UUID        NOT NULL REFERENCES gbekoun.users(id) ON DELETE CASCADE,
    reported_id      UUID        REFERENCES gbekoun.users(id) ON DELETE CASCADE,
    message_id       VARCHAR(24),
    conversation_id  UUID        REFERENCES gbekoun.conversations(id) ON DELETE SET NULL,
    reason           VARCHAR(50) NOT NULL,
    details          TEXT,
    status           VARCHAR(20) DEFAULT 'pending',
    resolved_by      UUID        REFERENCES gbekoun.users(id) ON DELETE SET NULL,
    resolved_at      TIMESTAMP,
    created_at       TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMP   DEFAULT CURRENT_TIMESTAMP
);

DROP TRIGGER IF EXISTS trg_reports_updated_at ON gbekoun.reports;
CREATE TRIGGER trg_reports_updated_at
    BEFORE UPDATE ON gbekoun.reports
    FOR EACH ROW EXECUTE FUNCTION gbekoun.set_updated_at();

-- -------------------------------------------------------------
-- push_devices : jetons Expo pour les notifications push
-- (équivalent de database/migrations/001_push_devices.sql)
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gbekoun.push_devices (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID NOT NULL REFERENCES gbekoun.users(id) ON DELETE CASCADE,
    expo_push_token  TEXT NOT NULL UNIQUE,
    platform         VARCHAR(20) NOT NULL DEFAULT 'android'
        CHECK (platform IN ('android', 'ios')),
    device_id        VARCHAR(255),
    is_active        BOOLEAN   NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- -------------------------------------------------------------
-- Appareils : un seul appareil mobile actif par compte
-- (+ pour les admins seulement : une session web d'administration, 1 h max,
--  prolongeable depuis le téléphone, coupée après 5 min d'inactivité)
-- (équivalent de database/migrations/002_user_devices.sql)
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gbekoun.user_devices (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID        NOT NULL REFERENCES gbekoun.users(id) ON DELETE CASCADE,
    secret_hash     CHAR(64)    NOT NULL,       -- SHA-256 du device_secret (jamais le secret)
    device_name     VARCHAR(100),
    kind            VARCHAR(10) NOT NULL DEFAULT 'mobile'
        CHECK (kind IN ('mobile', 'web')),      -- web = PC, administration uniquement
    platform        VARCHAR(20) CHECK (platform IN ('android', 'ios', 'web')),
    created_at      TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at    TIMESTAMP,                  -- dernière requête (PC : inactif 5 min = déconnecté)
    expires_at      TIMESTAMP   NULL,           -- fin de session (web) ; NULL = pas de limite
    revoked_at      TIMESTAMP   NULL,           -- NULL = appareil actif
    revoked_reason  VARCHAR(50)
);
-- La base garantit elle-même « un seul appareil actif par compte et par type »
-- (un téléphone, et pour un admin un seul PC).
CREATE UNIQUE INDEX IF NOT EXISTS uq_user_devices_one_active_per_kind
    ON gbekoun.user_devices(user_id, kind) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_user_devices_secret_active
    ON gbekoun.user_devices(secret_hash) WHERE revoked_at IS NULL;

-- Codes d'activation remis par un administrateur (usage unique, courte durée)
CREATE TABLE IF NOT EXISTS gbekoun.device_activation_codes (
    id          SERIAL PRIMARY KEY,
    user_id     UUID      NOT NULL REFERENCES gbekoun.users(id) ON DELETE CASCADE,
    code_hash   CHAR(64)  NOT NULL,
    created_by  UUID      REFERENCES gbekoun.users(id) ON DELETE SET NULL,
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at  TIMESTAMP NOT NULL,
    used_at     TIMESTAMP NULL,
    attempts    INTEGER   NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_activation_codes_user
    ON gbekoun.device_activation_codes(user_id, created_at DESC);

-- Demandes de connexion d'un nouvel appareil, validées par l'appareil actif
CREATE TABLE IF NOT EXISTS gbekoun.device_approval_requests (
    id                  UUID PRIMARY KEY,
    user_id             UUID        NOT NULL REFERENCES gbekoun.users(id) ON DELETE CASCADE,
    secret_hash         CHAR(64)    NOT NULL,
    device_name         VARCHAR(100),
    platform            VARCHAR(20),
    kind                VARCHAR(10) NOT NULL DEFAULT 'mobile' CHECK (kind IN ('mobile', 'web')),
    -- activate = connexion d'un nouvel appareil ; extend = prolonger la session PC en cours
    purpose             VARCHAR(10) NOT NULL DEFAULT 'activate' CHECK (purpose IN ('activate', 'extend')),
    target_device_id    UUID        REFERENCES gbekoun.user_devices(id) ON DELETE CASCADE,
    status              VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected', 'expired')),
    approved_device_id  UUID        REFERENCES gbekoun.user_devices(id) ON DELETE SET NULL,
    created_at          TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at          TIMESTAMP   NOT NULL,
    decided_at          TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_approval_requests_user
    ON gbekoun.device_approval_requests(user_id, created_at DESC);

-- -------------------------------------------------------------
-- Nettoyage : les avatars ne peuvent plus être des URL externes
-- (anciennes données, avant l'upload). Rejouable sans effet de bord.
-- -------------------------------------------------------------
UPDATE gbekoun.users SET avatar_url = NULL
WHERE avatar_url IS NOT NULL AND avatar_url NOT LIKE '/api/media/avatars/%';
UPDATE gbekoun.conversations SET avatar_url = NULL
WHERE avatar_url IS NOT NULL AND avatar_url NOT LIKE '/api/media/groups/%';

-- -------------------------------------------------------------
-- Index
-- -------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_users_is_online           ON gbekoun.users(is_online);
CREATE INDEX IF NOT EXISTS idx_invite_codes_expires      ON gbekoun.invite_codes(expires_at);
CREATE INDEX IF NOT EXISTS idx_conversations_type        ON gbekoun.conversations(type);
CREATE INDEX IF NOT EXISTS idx_conversations_updated     ON gbekoun.conversations(updated_at);
CREATE INDEX IF NOT EXISTS idx_participants_user         ON gbekoun.conversation_participants(user_id);
CREATE INDEX IF NOT EXISTS idx_blocked_blocked           ON gbekoun.blocked_users(blocked_id);
CREATE INDEX IF NOT EXISTS idx_starred_user              ON gbekoun.starred_messages(user_id);
CREATE INDEX IF NOT EXISTS idx_starred_conversation      ON gbekoun.starred_messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_reports_status            ON gbekoun.reports(status);
CREATE INDEX IF NOT EXISTS idx_reports_reporter          ON gbekoun.reports(reporter_id);
CREATE INDEX IF NOT EXISTS idx_push_devices_active_user
    ON gbekoun.push_devices(user_id) WHERE is_active = TRUE;
