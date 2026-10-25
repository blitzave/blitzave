-- Deferred from 001_init.sql until ingestion was actually built — see AGENTS.md
-- "Starting schema" note. Neither table touches the asset/liability split.

-- SimpleFIN only returns the current balance, so history that isn't recorded
-- at each sync is gone for good. That's why this table exists before it's used.
CREATE TABLE balances (
  account_id     INTEGER NOT NULL REFERENCES accounts(id),
  as_of          TEXT NOT NULL,             -- ISO date
  balance_cents  INTEGER NOT NULL,
  PRIMARY KEY (account_id, as_of)
) STRICT;

CREATE TABLE job_runs (
  job         TEXT PRIMARY KEY,
  last_ok_at  INTEGER,
  watermark   TEXT                          -- e.g. last ingested date
) STRICT;
