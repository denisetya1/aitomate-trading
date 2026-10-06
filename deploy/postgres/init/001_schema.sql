BEGIN;

CREATE TABLE market_snapshots (
    snapshot_id text PRIMARY KEY,
    schema_version integer NOT NULL CHECK (schema_version > 0),
    symbol text NOT NULL,
    captured_at timestamptz NOT NULL,
    published_at timestamptz NOT NULL,
    bid numeric(20, 8) NOT NULL,
    ask numeric(20, 8) NOT NULL CHECK (ask >= bid),
    symbol_spec jsonb NOT NULL,
    account_state jsonb NOT NULL,
    positions jsonb NOT NULL DEFAULT '[]'::jsonb,
    orders jsonb NOT NULL DEFAULT '[]'::jsonb,
    candles jsonb NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX market_snapshots_symbol_captured_idx
    ON market_snapshots (symbol, captured_at DESC);

CREATE TABLE trade_candidates (
    setup_id text PRIMARY KEY,
    snapshot_id text NOT NULL REFERENCES market_snapshots(snapshot_id),
    strategy_version text NOT NULL,
    mode text NOT NULL CHECK (mode IN ('REPLAY', 'SHADOW', 'DEMO', 'REAL')),
    status text NOT NULL,
    symbol text NOT NULL,
    direction text NOT NULL CHECK (direction IN ('BUY', 'SELL')),
    entry numeric(20, 8) NOT NULL,
    stop_loss numeric(20, 8) NOT NULL,
    take_profit numeric(20, 8) NOT NULL,
    volume numeric(20, 8) NOT NULL CHECK (volume > 0),
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    risk_result jsonb NOT NULL,
    strategy_context jsonb NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    CHECK (expires_at > created_at)
);

CREATE INDEX trade_candidates_status_created_idx
    ON trade_candidates (status, created_at DESC);

CREATE TABLE ai_validations (
    validation_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    setup_id text NOT NULL REFERENCES trade_candidates(setup_id),
    profile text NOT NULL,
    model text,
    decision text NOT NULL CHECK (decision IN ('APPROVE', 'REJECT', 'ABSTAIN')),
    reason text NOT NULL,
    latency_ms integer CHECK (latency_ms IS NULL OR latency_ms >= 0),
    usage jsonb,
    decided_at timestamptz NOT NULL,
    UNIQUE (setup_id, profile)
);

CREATE TABLE execution_events (
    event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    request_id text NOT NULL,
    setup_id text NOT NULL REFERENCES trade_candidates(setup_id),
    event_type text NOT NULL,
    mode text NOT NULL CHECK (mode IN ('REPLAY', 'SHADOW', 'DEMO', 'REAL')),
    payload jsonb NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (request_id, event_type)
);

CREATE INDEX execution_events_setup_idx
    ON execution_events (setup_id, occurred_at);

CREATE TABLE risk_daily_state (
    trading_day date NOT NULL,
    account_scope text NOT NULL,
    day_start_equity numeric(20, 8) NOT NULL,
    realized_pnl numeric(20, 8) NOT NULL DEFAULT 0,
    risk_committed numeric(20, 8) NOT NULL DEFAULT 0,
    trading_locked boolean NOT NULL DEFAULT false,
    lock_reason text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (trading_day, account_scope)
);

CREATE TABLE engine_heartbeats (
    component text PRIMARY KEY,
    mode text NOT NULL CHECK (mode IN ('REPLAY', 'SHADOW', 'DEMO', 'REAL')),
    status text NOT NULL,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    observed_at timestamptz NOT NULL
);

COMMIT;
