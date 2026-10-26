-- A category's optional recurring monthly budget, always whole cents.
ALTER TABLE categories ADD COLUMN budget_cents INTEGER
  CHECK (budget_cents IS NULL OR budget_cents > 0);
