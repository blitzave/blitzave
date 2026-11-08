-- When a category's plan (its amount and schedule) was last set (P0.3
-- rounds, 2026-11-08). A round closes with a result ("+$80 of Shopping"),
-- and only rounds since the plan was set count, so a budget set today never
-- reports rounds from months it didn't exist in. Any change to the plan
-- starts it again.
ALTER TABLE categories ADD COLUMN plan_set_on TEXT    -- ISO date
  CHECK (plan_set_on IS NULL OR plan_set_on IS date(plan_set_on));

-- Nobody knows when the existing plans were set, so their rounds count from
-- the day this runs.
UPDATE categories SET plan_set_on = date('now', 'localtime') WHERE budget_cents IS NOT NULL;
