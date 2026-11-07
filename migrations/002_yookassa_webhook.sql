BEGIN;
SET LOCAL lock_timeout = '3s';

CREATE TABLE IF NOT EXISTS yookassa_webhook_cutover (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    captured_since TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

INSERT INTO yookassa_webhook_cutover DEFAULT VALUES ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS yookassa_applied_payments (
    payment_id UUID PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMIT;
