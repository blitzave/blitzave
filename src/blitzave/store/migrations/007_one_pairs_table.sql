-- Maintainer call, 2026-11-02: every matched pair lives in `transfers`. A pair
-- is an exactly opposite debit and credit; whether it reads as a transfer, a
-- refund or a credit is decided when it's shown, never stored:
--   legs in two accounts             -> transfer
--   one account, same merchant       -> refund
--   one account, different merchant  -> credit
-- Both legs of every pair leave activity (the view already reads only
-- `transfers`). Partial credits aren't supported for now.

-- Same account is now allowed; same currency and same user are now checked
-- here too (the app already checked them).
DROP TRIGGER transfers_must_balance;
CREATE TRIGGER transfers_must_balance
BEFORE INSERT ON transfers
WHEN NOT EXISTS (
  SELECT 1
  FROM transactions d, transactions c
  WHERE d.id = NEW.debit_transaction_id
    AND c.id = NEW.credit_transaction_id
    AND d.user_id = NEW.user_id AND c.user_id = NEW.user_id
    AND d.currency = c.currency
    AND d.amount_cents < 0
    AND d.amount_cents + c.amount_cents = 0
    AND -d.amount_cents = NEW.transfer_amount_cents
)
BEGIN
  SELECT RAISE(ABORT, 'transfer legs must be an equal debit and credit in one currency, matching transfer_amount_cents');
END;

-- Pairs are never edited: unlink (delete the row) and link again.
CREATE TRIGGER transfers_cannot_be_updated
BEFORE UPDATE ON transfers
BEGIN
  SELECT RAISE(ABORT, 'unlink the pair before linking another');
END;

-- Move every exact refund link across. A partial link kept both rows in
-- activity, so leaving it behind changes no total; only the list grouping
-- goes. The pre-migration snapshot keeps the old links.
DROP TRIGGER transfers_exclude_refunds;
INSERT INTO transfers
  (user_id, debit_transaction_id, credit_transaction_id,
   transfer_amount_cents, transfer_date, description, matched_by, created_at)
SELECT r.user_id, d.id, c.id, -d.amount_cents, MAX(d.posted_on, c.posted_on), NULL, 'user',
       r.created_at
FROM refunds r
JOIN transactions d ON d.id = r.debit_transaction_id
JOIN transactions c ON c.id = r.credit_transaction_id
WHERE d.amount_cents + c.amount_cents = 0;

DROP TABLE refunds;  -- its own triggers go with it
