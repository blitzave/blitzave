-- "Apply to all": when the user categorizes one transaction and says "all
-- transactions like this", the choice is remembered here so every future sync
-- applies it too. It lives in the database, not in code, because it's the
-- user's own correction and must survive restarts and travel with an export.
--
-- Matching is exact string equality on the bank's description. One rule per
-- description; applying to all again replaces it. Deleting the category
-- deletes its rule (nothing would be left for the rule to point at).
CREATE TABLE category_rules (
  id          INTEGER PRIMARY KEY,
  user_id     INTEGER NOT NULL REFERENCES users(id),
  description TEXT NOT NULL,
  category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
  created_at  INTEGER NOT NULL,             -- unix seconds
  UNIQUE (user_id, description)
) STRICT;
