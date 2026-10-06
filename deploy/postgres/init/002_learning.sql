BEGIN;

CREATE TABLE IF NOT EXISTS trade_learning_predictions (
    setup_id text PRIMARY KEY,
    decided_at timestamptz NOT NULL,
    strategy_version text NOT NULL,
    direction text NOT NULL CHECK (direction IN ('BUY', 'SELL')),
    features jsonb NOT NULL,
    ai_probability double precision NOT NULL CHECK (ai_probability BETWEEN 0 AND 1),
    calibrated_probability double precision NOT NULL CHECK (calibrated_probability BETWEEN 0 AND 1),
    estimated_loss numeric(20, 8) NOT NULL CHECK (estimated_loss >= 0),
    net_reward_risk double precision NOT NULL,
    gate_decision text NOT NULL CHECK (gate_decision IN ('EXECUTE', 'REJECT')),
    gate_reason text NOT NULL,
    ai_reason text NOT NULL
);

CREATE TABLE IF NOT EXISTS broker_deals (
    deal_ticket bigint PRIMARY KEY,
    position_id bigint NOT NULL,
    entry_type integer NOT NULL,
    direction_type integer NOT NULL,
    volume numeric(20, 8) NOT NULL,
    price numeric(20, 8) NOT NULL,
    profit numeric(20, 8) NOT NULL,
    commission numeric(20, 8) NOT NULL,
    swap numeric(20, 8) NOT NULL,
    comment text NOT NULL,
    occurred_at timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS broker_deals_position_idx
    ON broker_deals (position_id, occurred_at);

CREATE TABLE IF NOT EXISTS learning_outcomes (
    setup_id text PRIMARY KEY REFERENCES trade_learning_predictions(setup_id),
    position_id bigint NOT NULL,
    closed_at timestamptz NOT NULL,
    pnl numeric(20, 8) NOT NULL,
    r_multiple double precision NOT NULL,
    won boolean NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS learning_evaluations (
    evaluation_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    strategy_version text NOT NULL,
    evaluated_at timestamptz NOT NULL DEFAULT now(),
    sample_size integer NOT NULL,
    win_rate double precision,
    expectancy_r double precision,
    lower_probability_bound double precision,
    promoted boolean NOT NULL DEFAULT false,
    details jsonb NOT NULL DEFAULT '{}'::jsonb
);

COMMIT;
