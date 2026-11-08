-- A one-time goal may have no target date (maintainer call, 2026-11-08). An
-- emergency fund is an amount to hold, with no date it's due by.
--
--   once  budget_cents by target_on, or, for a savings-kind category, with
--         target_on NULL: the amount, whenever it gets there.
--
-- An expense or income amount that happens once still needs its date. 008's
-- two triggers are made again with that one change; the rest is the same.
DROP TRIGGER categories_schedule_on_insert;
DROP TRIGGER categories_schedule_on_update;

CREATE TRIGGER categories_schedule_on_insert
BEFORE INSERT ON categories
WHEN NOT (
  (NEW.budget_cents IS NULL AND NEW.schedule IS NULL AND NEW.period_months IS NULL
     AND NEW.target_on IS NULL AND NEW.ends_on IS NULL)
  OR (NEW.budget_cents IS NOT NULL AND NEW.schedule = 'once'
     AND (NEW.target_on IS NOT NULL OR NEW.kind = 'savings')
     AND NEW.period_months IS NULL AND NEW.ends_on IS NULL)
  OR (NEW.budget_cents IS NOT NULL AND NEW.schedule = 'recurring' AND NEW.period_months IS NOT NULL
     AND NEW.target_on IS NULL)
)
BEGIN
  SELECT RAISE(ABORT, 'a one-time amount needs target_on (a savings goal may go without); a recurring one needs period_months; no amount means no schedule');
END;

CREATE TRIGGER categories_schedule_on_update
BEFORE UPDATE ON categories
WHEN NOT (
  (NEW.budget_cents IS NULL AND NEW.schedule IS NULL AND NEW.period_months IS NULL
     AND NEW.target_on IS NULL AND NEW.ends_on IS NULL)
  OR (NEW.budget_cents IS NOT NULL AND NEW.schedule = 'once'
     AND (NEW.target_on IS NOT NULL OR NEW.kind = 'savings')
     AND NEW.period_months IS NULL AND NEW.ends_on IS NULL)
  OR (NEW.budget_cents IS NOT NULL AND NEW.schedule = 'recurring' AND NEW.period_months IS NOT NULL
     AND NEW.target_on IS NULL)
)
BEGIN
  SELECT RAISE(ABORT, 'a one-time amount needs target_on (a savings goal may go without); a recurring one needs period_months; no amount means no schedule');
END;
