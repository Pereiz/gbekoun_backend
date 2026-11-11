-- Migration 002 : liaison compte <-> appareil (un mobile par compte, + un PC pour les admins).
-- Déjà incluse dans database/postgres/schema.sql ; à appliquer seulement sur une base
-- créée avant cette version :  psql "$DATABASE_URL" -f database/migrations/002_user_devices.sql

BEGIN;

-- -------------------------------------------------------------
-- Appareils : un seul appareil mobile actif par compte
-- (+ pour les admins seulement : une session web d'administration, 1 h max)
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
    last_seen_at    TIMESTAMP,
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
    status              VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected', 'expired')),
    approved_device_id  UUID        REFERENCES gbekoun.user_devices(id) ON DELETE SET NULL,
    created_at          TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at          TIMESTAMP   NOT NULL,
    decided_at          TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_approval_requests_user
    ON gbekoun.device_approval_requests(user_id, created_at DESC);

COMMIT;
