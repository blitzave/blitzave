-- Maintainer call, 2026-11-02: a refund/credit is the same-account twin of a
-- transfer. Both are an exactly opposite debit and credit, and both pairs
-- leave activity. Which one a pair is depends only on whether the two legs
-- share an account. Partial credits aren't supported for now.

-- A partial link kept both rows in activity, so removing it changes no total;
-- only the list grouping goes. The pre-migration snapshot keeps the old links.
DELETE FROM refunds
WHERE id IN (
  SELECT r.id
  FROM refunds r
  JOIN transactions d ON d.id = r.debit_transaction_id
  JOIN transactions c ON c.id = r.credit_transaction_id
  WHERE d.amount_cents + c.amount_cents <> 0
);

DROP TRIGGER refunds_require_valid_pair;
CREATE TRIGGER refunds_require_valid_pair
BEFORE INSERT ON refunds
WHEN NOT EXISTS (
  SELECT 1 FROM transactions d, transactions c
  WHERE d.id = NEW.debit_transaction_id
    AND c.id = NEW.credit_transaction_id
    AND d.user_id = NEW.user_id AND c.user_id = NEW.user_id
    AND d.account_id = c.account_id
    AND d.currency = c.currency
    AND d.amount_cents < 0
    AND d.amount_cents + c.amount_cents = 0
)
BEGIN
  SELECT RAISE(ABORT, 'refund requires a charge and an exactly opposite credit, in the same account and currency');
END;

-- 'rule' = linked by the sync's matcher; 'user' = linked by hand. Every
-- existing link was made by hand.
ALTER TABLE refunds ADD COLUMN matched_by TEXT NOT NULL DEFAULT 'user'
  CHECK (matched_by IN ('rule', 'user'));

-- Everything analysis may read: transactions that aren't one side of a
-- transfer or a refund.
DROP VIEW activity;
CREATE VIEW activity AS
SELECT t.*
FROM transactions t
WHERE t.id NOT IN (SELECT debit_transaction_id FROM transfers)
  AND t.id NOT IN (SELECT credit_transaction_id FROM transfers)
  AND t.id NOT IN (SELECT debit_transaction_id FROM refunds)
  AND t.id NOT IN (SELECT credit_transaction_id FROM refunds);
