-- Goals live in accounts (maintainer call, 2026-11-06).
--
-- An asset account now has one of three jobs: checking, savings (a short
-- buffer for this month's bills) or targeted savings, where goals live. Only
-- a targeted savings account can hold goals. A goal is still a savings-kind
-- category with an amount (AGENTS.md); it now also has a level and a home:
--
--   critical  needs a targeted account to itself (emergency fund, down payment)
--   regular   can share one; the account's money fills its goals in
--             fill_order, top first (a PS5, a trip)
--
-- completed_on marks a goal that's been bought: it stops counting, and its
-- account and place in line are freed for others.

-- 1. SQLite can't change a CHECK in place, so assets is rebuilt with the new
--    job. Nothing references assets, so the copy is all there is to it; its one
--    trigger goes with the old table and is made again below.
CREATE TABLE assets_new (
  account_id INTEGER PRIMARY KEY REFERENCES accounts(id),
  bank_name  TEXT NOT NULL,
  asset_type TEXT NOT NULL CHECK (asset_type IN ('checking', 'savings', 'targeted'))
) STRICT;
INSERT INTO assets_new (account_id, bank_name, asset_type)
SELECT account_id, bank_name, asset_type FROM assets;
DROP TABLE assets;
ALTER TABLE assets_new RENAME TO assets;

CREATE TRIGGER assets_require_asset_type
BEFORE INSERT ON assets
WHEN NOT EXISTS (
  SELECT 1 FROM accounts WHERE id = NEW.account_id AND account_type = 'asset'
)
BEGIN
  SELECT RAISE(ABORT, 'assets.account_id must reference an accounts row with account_type = asset');
END;

-- 2. A goal's level, home and place in line.
ALTER TABLE categories ADD COLUMN goal_level TEXT
  CHECK (goal_level IN ('critical', 'regular'));
ALTER TABLE categories ADD COLUMN account_id INTEGER REFERENCES accounts(id);
ALTER TABLE categories ADD COLUMN fill_order INTEGER
  CHECK (fill_order >= 1);
ALTER TABLE categories ADD COLUMN completed_on TEXT    -- ISO date
  CHECK (completed_on IS NULL OR completed_on IS date(completed_on));

-- 3. The rules, checked on every insert and update. A goal is "active" until
--    it's completed; only active goals hold an account.
CREATE TRIGGER categories_goal_rules_on_insert
BEFORE INSERT ON categories
BEGIN
  SELECT RAISE(ABORT, 'a goal is a savings category with an amount, a level, an account and a place in line')
  WHERE NOT (
    (NEW.account_id IS NULL AND NEW.goal_level IS NULL AND NEW.fill_order IS NULL
       AND NEW.completed_on IS NULL)
    OR (NEW.account_id IS NOT NULL AND NEW.goal_level IS NOT NULL AND NEW.fill_order IS NOT NULL
       AND NEW.kind = 'savings' AND NEW.budget_cents IS NOT NULL)
  );
  SELECT RAISE(ABORT, 'goals live only in targeted savings accounts')
  WHERE NEW.account_id IS NOT NULL AND NEW.completed_on IS NULL
    AND NOT EXISTS (
      SELECT 1 FROM assets WHERE account_id = NEW.account_id AND asset_type = 'targeted'
    );
  SELECT RAISE(ABORT, 'a critical goal needs a targeted savings account to itself')
  WHERE NEW.account_id IS NOT NULL AND NEW.completed_on IS NULL
    AND EXISTS (
      SELECT 1 FROM categories c
      WHERE c.account_id = NEW.account_id AND c.completed_on IS NULL AND c.id IS NOT NEW.id
        AND (NEW.goal_level = 'critical' OR c.goal_level = 'critical')
    );
END;

CREATE TRIGGER categories_goal_rules_on_update
BEFORE UPDATE ON categories
BEGIN
  SELECT RAISE(ABORT, 'a goal is a savings category with an amount, a level, an account and a place in line')
  WHERE NOT (
    (NEW.account_id IS NULL AND NEW.goal_level IS NULL AND NEW.fill_order IS NULL
       AND NEW.completed_on IS NULL)
    OR (NEW.account_id IS NOT NULL AND NEW.goal_level IS NOT NULL AND NEW.fill_order IS NOT NULL
       AND NEW.kind = 'savings' AND NEW.budget_cents IS NOT NULL)
  );
  SELECT RAISE(ABORT, 'goals live only in targeted savings accounts')
  WHERE NEW.account_id IS NOT NULL AND NEW.completed_on IS NULL
    AND NOT EXISTS (
      SELECT 1 FROM assets WHERE account_id = NEW.account_id AND asset_type = 'targeted'
    );
  SELECT RAISE(ABORT, 'a critical goal needs a targeted savings account to itself')
  WHERE NEW.account_id IS NOT NULL AND NEW.completed_on IS NULL
    AND EXISTS (
      SELECT 1 FROM categories c
      WHERE c.account_id = NEW.account_id AND c.completed_on IS NULL AND c.id IS NOT NEW.id
        AND (NEW.goal_level = 'critical' OR c.goal_level = 'critical')
    );
END;

-- 4. An account that holds goals keeps its job until they move or complete,
--    whether it's switched to checking or savings or turned into a liability.
CREATE TRIGGER assets_goal_home_keeps_its_job
BEFORE UPDATE OF asset_type ON assets
WHEN NEW.asset_type != 'targeted'
  AND EXISTS (
    SELECT 1 FROM categories WHERE account_id = OLD.account_id AND completed_on IS NULL
  )
BEGIN
  SELECT RAISE(ABORT, 'this account holds goals; move them before changing its job');
END;

CREATE TRIGGER assets_goal_home_stays_an_asset
BEFORE DELETE ON assets
WHEN EXISTS (
  SELECT 1 FROM categories WHERE account_id = OLD.account_id AND completed_on IS NULL
)
BEGIN
  SELECT RAISE(ABORT, 'this account holds goals; move them before changing its job');
END;
