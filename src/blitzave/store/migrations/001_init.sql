-- See AGENTS.md "Starting schema" for the full rationale behind each table.

CREATE TABLE users (
  id             INTEGER PRIMARY KEY,
  username       TEXT NOT NULL UNIQUE,
  simplefin_hash TEXT,                      -- hash of the SimpleFIN access URL; never the URL itself
  meta           TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(meta)),
  created_at     INTEGER NOT NULL
) STRICT;

CREATE TABLE accounts (
  id             INTEGER PRIMARY KEY,
  user_id        INTEGER NOT NULL REFERENCES users(id),
  account_type   TEXT NOT NULL CHECK (account_type IN ('asset', 'liability')),
  account_number TEXT NOT NULL,              -- last 4 digits only; never the full number
  display_name   TEXT GENERATED ALWAYS AS (account_type || '-' || account_number) STORED UNIQUE,
  source         TEXT NOT NULL DEFAULT 'simplefin',
  external_id    TEXT NOT NULL,              -- SimpleFIN account id
  meta           TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(meta)),
  UNIQUE (source, external_id)
) STRICT;

CREATE TABLE assets (                        -- checking and savings
  account_id INTEGER PRIMARY KEY REFERENCES accounts(id),
  bank_name  TEXT NOT NULL,
  asset_type TEXT NOT NULL CHECK (asset_type IN ('checking', 'savings'))
) STRICT;

CREATE TABLE liabilities (                   -- credit cards today; loans modeled, not ingested
  account_id     INTEGER PRIMARY KEY REFERENCES accounts(id),
  issuer_name    TEXT NOT NULL,
  liability_type TEXT NOT NULL CHECK (liability_type IN ('credit_card', 'loan'))
) STRICT;

-- accounts.account_type and the extension row must agree; SQLite can't
-- express a cross-table CHECK, so a trigger does it.
CREATE TRIGGER assets_require_asset_type
BEFORE INSERT ON assets
WHEN NOT EXISTS (
  SELECT 1 FROM accounts WHERE id = NEW.account_id AND account_type = 'asset'
)
BEGIN
  SELECT RAISE(ABORT, 'assets.account_id must reference an accounts row with account_type = asset');
END;

CREATE TRIGGER liabilities_require_liability_type
BEFORE INSERT ON liabilities
WHEN NOT EXISTS (
  SELECT 1 FROM accounts WHERE id = NEW.account_id AND account_type = 'liability'
)
BEGIN
  SELECT RAISE(ABORT, 'liabilities.account_id must reference an accounts row with account_type = liability');
END;

CREATE TABLE categories (
  id      INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  name    TEXT NOT NULL,
  kind    TEXT NOT NULL CHECK (kind IN ('expense', 'income', 'savings')),
  -- 'savings' covers money moved to or from the user's own unlinked accounts
  UNIQUE (user_id, name)
) STRICT;

CREATE TABLE tags (
  id      INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  name    TEXT NOT NULL,
  UNIQUE (user_id, name)
) STRICT;

CREATE TABLE transactions (
  id                INTEGER PRIMARY KEY,
  user_id           INTEGER NOT NULL REFERENCES users(id),
  account_id        INTEGER NOT NULL REFERENCES accounts(id),
  external_id       TEXT NOT NULL,           -- SimpleFIN transaction id; unique only within an account
  amount_cents      INTEGER NOT NULL,        -- negative = money left the account; positive = arrived
  currency          TEXT NOT NULL DEFAULT 'USD' CHECK (length(currency) = 3),
  transaction_type  TEXT NOT NULL CHECK (transaction_type IN ('debit', 'credit')),
  posted_on         TEXT NOT NULL,           -- ISO date, local timezone
  description       TEXT NOT NULL,
  merchant_name     TEXT,
  categories        TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(categories)),  -- {category_id: split_fraction}
  categories_set_by TEXT CHECK (categories_set_by IN ('rule', 'user')),
  meta              TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(meta)),
  created_at        INTEGER NOT NULL,        -- unix seconds
  UNIQUE (account_id, external_id),
  -- amount_cents is still the sign of truth; transaction_type can't drift from it.
  CHECK (
    (transaction_type = 'debit' AND amount_cents < 0) OR
    (transaction_type = 'credit' AND amount_cents > 0)
  )
) STRICT;
CREATE INDEX tx_posted ON transactions(posted_on);

CREATE TABLE transaction_tags (               -- a transaction may carry any number of tags
  transaction_id INTEGER NOT NULL REFERENCES transactions(id),
  tag_id         INTEGER NOT NULL REFERENCES tags(id),
  PRIMARY KEY (transaction_id, tag_id)
) STRICT;

-- A transfer links one debit and one credit between two of the user's accounts.
-- Each transaction can be a leg of at most one transfer.
CREATE TABLE transfers (
  id                     INTEGER PRIMARY KEY,
  user_id                INTEGER NOT NULL REFERENCES users(id),
  debit_transaction_id   INTEGER NOT NULL UNIQUE REFERENCES transactions(id),
  credit_transaction_id  INTEGER NOT NULL UNIQUE REFERENCES transactions(id),
  transfer_amount_cents  INTEGER NOT NULL,    -- must equal both legs' unsigned amount
  transfer_date          TEXT NOT NULL,
  description            TEXT,
  matched_by             TEXT NOT NULL CHECK (matched_by IN ('rule', 'user')),
  meta                   TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(meta)),
  created_at             INTEGER NOT NULL
) STRICT;

-- The invariant: a negative debit, an exactly opposite credit, two different
-- accounts, and both legs agree with transfer_amount_cents.
CREATE TRIGGER transfers_must_balance
BEFORE INSERT ON transfers
WHEN NOT EXISTS (
  SELECT 1
  FROM transactions d, transactions c
  WHERE d.id = NEW.debit_transaction_id
    AND c.id = NEW.credit_transaction_id
    AND d.amount_cents < 0
    AND d.amount_cents + c.amount_cents = 0
    AND d.account_id <> c.account_id
    AND -d.amount_cents = NEW.transfer_amount_cents
)
BEGIN
  SELECT RAISE(ABORT, 'transfer legs must be an equal debit and credit in two accounts, matching transfer_amount_cents');
END;

-- Everything analysis may read: transactions that aren't transfer legs.
CREATE VIEW activity AS
SELECT t.*
FROM transactions t
WHERE t.id NOT IN (SELECT debit_transaction_id FROM transfers)
  AND t.id NOT IN (SELECT credit_transaction_id FROM transfers);
