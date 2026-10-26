-- Rules now match on a normalized key (see refine/categorize.py
-- normalize_description), not the exact description: "STARBUCKS #123" and
-- "starbucks #163" are the same merchant. The key is computed in Python, so it
-- has to be stored; description stays as the example the rule was made from,
-- for showing the rule to the user later.
--
-- Drop and recreate rather than ALTER: the rules feature is a day old and the
-- only real database had zero rules when this was written, so there's nothing
-- to carry over.
DROP TABLE category_rules;

CREATE TABLE category_rules (
  id          INTEGER PRIMARY KEY,
  user_id     INTEGER NOT NULL REFERENCES users(id),
  match_key   TEXT NOT NULL CHECK (match_key <> ''),  -- normalize_description(description)
  description TEXT NOT NULL,                -- the transaction description the rule was made from
  category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
  created_at  INTEGER NOT NULL,             -- unix seconds
  UNIQUE (user_id, match_key)
) STRICT;
