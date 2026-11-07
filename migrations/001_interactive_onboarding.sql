BEGIN;
SET LOCAL lock_timeout = '3s';

CREATE TABLE IF NOT EXISTS onboarding_enrollments (
    user_id BIGINT NOT NULL,
    campaign_version TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (user_id, campaign_version)
);

CREATE TABLE IF NOT EXISTS onboarding_deliveries (
    user_id BIGINT NOT NULL,
    campaign_version TEXT NOT NULL,
    step_key TEXT NOT NULL,
    due_at TIMESTAMPTZ NOT NULL,
    next_attempt_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'sending', 'sent', 'expired', 'failed', 'uncertain')),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    claimed_at TIMESTAMPTZ,
    sent_at TIMESTAMPTZ,
    telegram_message_id BIGINT,
    PRIMARY KEY (user_id, campaign_version, step_key),
    FOREIGN KEY (user_id, campaign_version)
        REFERENCES onboarding_enrollments (user_id, campaign_version)
);

CREATE INDEX IF NOT EXISTS onboarding_deliveries_ready_idx
    ON onboarding_deliveries (campaign_version, next_attempt_at, user_id, step_key)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS onboarding_deliveries_expiry_idx
    ON onboarding_deliveries (campaign_version, due_at)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS onboarding_deliveries_claim_idx
    ON onboarding_deliveries (campaign_version, claimed_at)
    WHERE status = 'sending';

COMMIT;
