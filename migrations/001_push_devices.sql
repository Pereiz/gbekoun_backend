BEGIN;

CREATE TABLE IF NOT EXISTS gbekoun.push_devices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES gbekoun.users(id) ON DELETE CASCADE,
    expo_push_token TEXT NOT NULL UNIQUE,
    platform VARCHAR(20) NOT NULL DEFAULT 'android'
        CHECK (platform IN ('android', 'ios')),
    device_id VARCHAR(255),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_push_devices_active_user
    ON gbekoun.push_devices(user_id)
    WHERE is_active = TRUE;

COMMIT;
