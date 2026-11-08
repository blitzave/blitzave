-- A category's amount now comes with a schedule (maintainer call, 2026-11-06).
-- Goals and expense categories share it: a goal is a savings-kind category.
--
--   once       budget_cents by target_on ("PS5, $500 by Dec 20").
--   recurring  budget_cents every period_months, until ends_on if one is set
--              ("Shopping, $400 every 3 months"). Rounds are calendar-aligned:
--              a 3-month period runs Jan-Mar, Apr-Jun, and so on, which is
--              why the period must divide 12.
--
-- budget_cents keeps its name and becomes the amount for either schedule.
-- No amount means no schedule: all four new columns stay NULL.
ALTER TABLE categories ADD COLUMN schedule TEXT
  CHECK (schedule IN ('once', 'recurring'));
ALTER TABLE categories ADD COLUMN period_months INTEGER
  CHECK (period_months IN (1, 2, 3, 4, 6, 12));
ALTER TABLE categories ADD COLUMN target_on TEXT      -- ISO date
  CHECK (target_on IS NULL OR target_on IS date(target_on));
ALTER TABLE categories ADD COLUMN ends_on TEXT        -- ISO date
  CHECK (ends_on IS NULL OR ends_on IS date(ends_on));

-- Every budget set before this migration was a monthly one.
UPDATE categories SET schedule = 'recurring', period_months = 1
WHERE budget_cents IS NOT NULL;

-- The columns must agree with each other. ALTER TABLE can't add a CHECK that
-- spans columns, so triggers do it, the same way 001 guards account types.
CREATE TRIGGER categories_schedule_on_insert
BEFORE INSERT ON categories
WHEN NOT (
  (NEW.budget_cents IS NULL AND NEW.schedule IS NULL AND NEW.period_months IS NULL
     AND NEW.target_on IS NULL AND NEW.ends_on IS NULL)
  OR (NEW.budget_cents IS NOT NULL AND NEW.schedule = 'once' AND NEW.target_on IS NOT NULL
     AND NEW.period_months IS NULL AND NEW.ends_on IS NULL)
  OR (NEW.budget_cents IS NOT NULL AND NEW.schedule = 'recurring' AND NEW.period_months IS NOT NULL
     AND NEW.target_on IS NULL)
)
BEGIN
  SELECT RAISE(ABORT, 'a one-time amount needs target_on; a recurring one needs period_months; no amount means no schedule');
END;

CREATE TRIGGER categories_schedule_on_update
BEFORE UPDATE ON categories
WHEN NOT (
  (NEW.budget_cents IS NULL AND NEW.schedule IS NULL AND NEW.period_months IS NULL
     AND NEW.target_on IS NULL AND NEW.ends_on IS NULL)
  OR (NEW.budget_cents IS NOT NULL AND NEW.schedule = 'once' AND NEW.target_on IS NOT NULL
     AND NEW.period_months IS NULL AND NEW.ends_on IS NULL)
  OR (NEW.budget_cents IS NOT NULL AND NEW.schedule = 'recurring' AND NEW.period_months IS NOT NULL
     AND NEW.target_on IS NULL)
)
BEGIN
  SELECT RAISE(ABORT, 'a one-time amount needs target_on; a recurring one needs period_months; no amount means no schedule');
END;
