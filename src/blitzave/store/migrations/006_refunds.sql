-- A refund/credit is a manual link within one account. Both bank rows remain
-- activity: the positive credit offsets spending in its assigned category.
CREATE TABLE refunds (
  id                     INTEGER PRIMARY KEY,
  user_id                INTEGER NOT NULL REFERENCES users(id),
  debit_transaction_id   INTEGER NOT NULL UNIQUE REFERENCES transactions(id),
  credit_transaction_id  INTEGER NOT NULL UNIQUE REFERENCES transactions(id),
  created_at             INTEGER NOT NULL
) STRICT;

CREATE TRIGGER refunds_require_valid_pair
BEFORE INSERT ON refunds
WHEN NOT EXISTS (
  SELECT 1 FROM transactions d, transactions c
  WHERE d.id = NEW.debit_transaction_id
    AND c.id = NEW.credit_transaction_id
    AND d.user_id = NEW.user_id AND c.user_id = NEW.user_id
    AND d.account_id = c.account_id
    AND d.currency = c.currency
    AND d.amount_cents < 0 AND c.amount_cents > 0
    AND c.amount_cents <= -d.amount_cents
)
BEGIN
  SELECT RAISE(ABORT, 'refund requires a charge and a positive credit no larger than the charge, in the same account and currency');
END;

CREATE TRIGGER refunds_exclude_transfers
BEFORE INSERT ON refunds
WHEN EXISTS (
  SELECT 1 FROM transfers
  WHERE debit_transaction_id IN (NEW.debit_transaction_id, NEW.credit_transaction_id)
     OR credit_transaction_id IN (NEW.debit_transaction_id, NEW.credit_transaction_id)
)
BEGIN
  SELECT RAISE(ABORT, 'a transfer leg cannot also be linked as a refund');
END;

CREATE TRIGGER transfers_exclude_refunds
BEFORE INSERT ON transfers
WHEN EXISTS (
  SELECT 1 FROM refunds
  WHERE debit_transaction_id IN (NEW.debit_transaction_id, NEW.credit_transaction_id)
     OR credit_transaction_id IN (NEW.debit_transaction_id, NEW.credit_transaction_id)
)
BEGIN
  SELECT RAISE(ABORT, 'a refund transaction cannot also be a transfer leg');
END;

-- Links are changed by unlinking and creating a new link, never by editing.
CREATE TRIGGER refunds_cannot_be_updated
BEFORE UPDATE ON refunds
BEGIN
  SELECT RAISE(ABORT, 'unlink the refund before linking another pair');
END;
