-- =============================================================
-- SCRIPT D'INSTALLATION - BASE DE DONNÉES DE MESSAGERIE
-- PostgreSQL
-- Base : gbekoundb
-- Utilisateur : gbekoundb_user
-- Schéma : gbekoun
-- =============================================================

-- 1. CRÉATION DE L'UTILISATEUR DÉDIÉ
-- À exécuter avec un superutilisateur (postgres)
-- =============================================================

DO $$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'gbekoundb_user') THEN
      CREATE USER gbekoundb_user WITH PASSWORD 'postgres_password_2024';
   END IF;
END
$$;

-- 2. CRÉATION DE LA BASE DE DONNÉES
-- =============================================================

CREATE DATABASE gbekoundb
    OWNER = gbekoundb_user
    ENCODING = 'UTF8'
    LC_COLLATE = 'fr_FR.UTF-8'
    LC_CTYPE = 'fr_FR.UTF-8'
    TEMPLATE = template0;

-- 3. CONNEXION À LA BASE GBEKOUNDB
-- =============================================================
\c gbekoundb;

-- 4. CRÉATION DU SCHÉMA
-- =============================================================

CREATE SCHEMA IF NOT EXISTS gbekoun AUTHORIZATION gbekoundb_user;

-- Définir le schéma par défaut
SET search_path TO gbekoun;

-- 5. ACTIVATION DES EXTENSIONS
-- =============================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto" SCHEMA gbekoun;

-- 6. ATTRIBUTION DES PRIVILÈGES
-- =============================================================

GRANT ALL PRIVILEGES ON DATABASE gbekoundb TO gbekoundb_user;
GRANT ALL PRIVILEGES ON SCHEMA gbekoun TO gbekoundb_user;
GRANT USAGE ON SCHEMA gbekoun TO gbekoundb_user;
ALTER SCHEMA gbekoun OWNER TO gbekoundb_user;

ALTER DEFAULT PRIVILEGES IN SCHEMA gbekoun GRANT ALL PRIVILEGES ON TABLES TO gbekoundb_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA gbekoun GRANT ALL PRIVILEGES ON SEQUENCES TO gbekoundb_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA gbekoun GRANT ALL PRIVILEGES ON FUNCTIONS TO gbekoundb_user;

-- 7. FONCTION POUR updated_at AUTOMATIQUE
-- =============================================================

CREATE OR REPLACE FUNCTION gbekoun.set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 8. CRÉATION DES TABLES
-- =============================================================

-- -------------------------------------------------------------
-- TABLE : users (utilisateurs)
-- -------------------------------------------------------------
CREATE TABLE gbekoun.users (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    phone_number      VARCHAR(20)   NOT NULL UNIQUE,
    display_name      VARCHAR(100)  NOT NULL,
    username          VARCHAR(50)   UNIQUE,
    bio               TEXT,
    avatar_url        TEXT,
    is_online         BOOLEAN       DEFAULT FALSE,
    last_seen         TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,
    created_at        TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,
    updated_at        TIMESTAMP     DEFAULT CURRENT_TIMESTAMP
);

CREATE TRIGGER trg_users_updated_at
    BEFORE UPDATE ON gbekoun.users
    FOR EACH ROW EXECUTE FUNCTION gbekoun.set_updated_at();

-- -------------------------------------------------------------
-- TABLE : invite_codes (codes d'invitation générés par admin)
-- -------------------------------------------------------------
CREATE TABLE gbekoun.invite_codes (
    id           SERIAL PRIMARY KEY,
    code         VARCHAR(10)   NOT NULL UNIQUE,
    used_by      UUID          NULL REFERENCES gbekoun.users(id) ON DELETE SET NULL,
    used_at      TIMESTAMP     NULL,
    created_by   UUID          NOT NULL REFERENCES gbekoun.users(id),
    created_at   TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,
    expires_at   TIMESTAMP     NOT NULL
);

-- -------------------------------------------------------------
-- TABLE : conversations (fils de discussion)
-- -------------------------------------------------------------
CREATE TABLE gbekoun.conversations (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type         VARCHAR(10)   NOT NULL CHECK (type IN ('direct', 'group')),
    name         VARCHAR(100),
    avatar_url   TEXT,
    created_by   UUID          REFERENCES gbekoun.users(id) ON DELETE SET NULL,
    created_at   TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMP     DEFAULT CURRENT_TIMESTAMP
);

CREATE TRIGGER trg_conversations_updated_at
    BEFORE UPDATE ON gbekoun.conversations
    FOR EACH ROW EXECUTE FUNCTION gbekoun.set_updated_at();

-- -------------------------------------------------------------
-- TABLE : conversation_participants (participants aux discussions)
-- -------------------------------------------------------------
CREATE TABLE gbekoun.conversation_participants (
    conversation_id      UUID      REFERENCES gbekoun.conversations(id) ON DELETE CASCADE,
    user_id              UUID      REFERENCES gbekoun.users(id) ON DELETE CASCADE,
    joined_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    unread_count         INTEGER   DEFAULT 0,
    last_read_message_id VARCHAR(24),
    is_admin             BOOLEAN   DEFAULT FALSE,
    muted_until          TIMESTAMP NULL,
    PRIMARY KEY (conversation_id, user_id)
);

-- -------------------------------------------------------------
-- TABLE : blocked_users (utilisateurs bloqués)
-- -------------------------------------------------------------
CREATE TABLE gbekoun.blocked_users (
    blocker_id   UUID      REFERENCES gbekoun.users(id) ON DELETE CASCADE,
    blocked_id   UUID      REFERENCES gbekoun.users(id) ON DELETE CASCADE,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (blocker_id, blocked_id)
);

-- -------------------------------------------------------------
-- TABLE : user_sessions (sessions utilisateur pour WebSocket)
-- -------------------------------------------------------------
CREATE TABLE gbekoun.user_sessions (
    id           SERIAL PRIMARY KEY,
    user_id      UUID      NOT NULL REFERENCES gbekoun.users(id) ON DELETE CASCADE,
    socket_id    VARCHAR(100),
    device_info  TEXT,
    ip_address   VARCHAR(45),
    is_active    BOOLEAN   DEFAULT TRUE,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TRIGGER trg_user_sessions_updated_at
    BEFORE UPDATE ON gbekoun.user_sessions
    FOR EACH ROW EXECUTE FUNCTION gbekoun.set_updated_at();

-- -------------------------------------------------------------
-- TABLE : reports (signalements)
-- -------------------------------------------------------------
CREATE TABLE gbekoun.reports (
    id              SERIAL PRIMARY KEY,
    reporter_id     UUID      NOT NULL REFERENCES gbekoun.users(id) ON DELETE CASCADE,
    reported_id     UUID      REFERENCES gbekoun.users(id) ON DELETE CASCADE,
    message_id      VARCHAR(24),
    conversation_id UUID      REFERENCES gbekoun.conversations(id) ON DELETE SET NULL,
    reason          VARCHAR(50) NOT NULL,
    details         TEXT,
    status          VARCHAR(20) DEFAULT 'pending',
    resolved_by     UUID      REFERENCES gbekoun.users(id) ON DELETE SET NULL,
    resolved_at     TIMESTAMP,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TRIGGER trg_reports_updated_at
    BEFORE UPDATE ON gbekoun.reports
    FOR EACH ROW EXECUTE FUNCTION gbekoun.set_updated_at();

-- Ajout les colonnes manquantes à la table conversation_participants
-- =============================================================

ALTER TABLE gbekoun.conversation_participants 
ADD COLUMN IF NOT EXISTS is_archived BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS is_pinned BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP NULL;


-- Ajjout de la table pour les messages épinglés
-- =============================================================
CREATE TABLE IF NOT EXISTS gbekoun.starred_messages (
    id SERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES gbekoun.users(id) ON DELETE CASCADE,
    conversation_id UUID NOT NULL REFERENCES gbekoun.conversations(id) ON DELETE CASCADE,
    message_id VARCHAR(24) NOT NULL,
    starred_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(user_id, message_id)
);

CREATE INDEX idx_starred_user ON gbekoun.starred_messages(user_id);
CREATE INDEX idx_starred_conversation ON gbekoun.starred_messages(conversation_id);



-- Ajouter les colonnes pour la suspension de comptes à la table users
-- =============================================================
ALTER TABLE gbekoun.users 
ADD COLUMN IF NOT EXISTS is_suspended BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS is_admin BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS suspended_at TIMESTAMP NULL,
ADD COLUMN IF NOT EXISTS suspended_reason TEXT NULL;


-- =============================================================
-- Ajouter les colonnes pour les paramètres de confidentialité
ALTER TABLE gbekoun.users 
ADD COLUMN IF NOT EXISTS privacy_last_seen VARCHAR(20) DEFAULT 'everyone',
ADD COLUMN IF NOT EXISTS privacy_profile_photo VARCHAR(20) DEFAULT 'everyone',
ADD COLUMN IF NOT EXISTS privacy_about VARCHAR(20) DEFAULT 'everyone',
ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP NULL;

-- Ajouter la table des utilisateurs bloqués si elle n'existe pas
CREATE TABLE IF NOT EXISTS gbekoun.blocked_users (
    blocker_id UUID NOT NULL REFERENCES gbekoun.users(id) ON DELETE CASCADE,
    blocked_id UUID NOT NULL REFERENCES gbekoun.users(id) ON DELETE CASCADE,
    created_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (blocker_id, blocked_id)
);

CREATE INDEX IF NOT EXISTS idx_blocked_blocker ON gbekoun.blocked_users(blocker_id);
CREATE INDEX IF NOT EXISTS idx_blocked_blocked ON gbekoun.blocked_users(blocked_id);

-- 9. CRÉATION DES INDEX
-- =============================================================

CREATE INDEX idx_users_phone ON gbekoun.users(phone_number);
CREATE INDEX idx_users_username ON gbekoun.users(username);
CREATE INDEX idx_users_is_online ON gbekoun.users(is_online);
CREATE INDEX idx_invite_codes_code ON gbekoun.invite_codes(code);
CREATE INDEX idx_invite_codes_expires ON gbekoun.invite_codes(expires_at);
CREATE INDEX idx_conversations_type ON gbekoun.conversations(type);
CREATE INDEX idx_conversations_updated ON gbekoun.conversations(updated_at);
CREATE INDEX idx_participants_user ON gbekoun.conversation_participants(user_id);
CREATE INDEX idx_participants_conversation ON gbekoun.conversation_participants(conversation_id);
CREATE INDEX idx_participants_unread ON gbekoun.conversation_participants(unread_count);
CREATE INDEX idx_blocked_blocker ON gbekoun.blocked_users(blocker_id);
CREATE INDEX idx_blocked_blocked ON gbekoun.blocked_users(blocked_id);
CREATE INDEX idx_sessions_user ON gbekoun.user_sessions(user_id);
CREATE INDEX idx_sessions_active ON gbekoun.user_sessions(is_active);
CREATE INDEX idx_reports_status ON gbekoun.reports(status);
CREATE INDEX idx_reports_reporter ON gbekoun.reports(reporter_id);

-- 10. VÉRIFICATION FINALE
-- =============================================================

SELECT 'Base PostgreSQL créée avec succès !' AS status;
SELECT COUNT(*) AS tables_crees FROM information_schema.tables 
WHERE table_schema = 'gbekoun';

-- =============================================================
-- FIN DU SCRIPT POSTGRESQL
-- =============================================================
