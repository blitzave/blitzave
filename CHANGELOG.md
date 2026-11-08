# Changelog

What changed in each BlitZave version, in plain words. Newest first.

To update: `uv tool upgrade blitzave`, then start `blitzave` as usual.

## Unreleased

### New

- **Savings goals with a home.** Mark the savings accounts you save toward goals
  in as **Goal account**, then add goals on the new **Savings** page. A critical
  goal, like an emergency fund, has an account to itself, and all of that
  account's money counts toward it, even past the goal. Regular goals, like a trip or a new phone, share an
  account: its money fills them from the top, in the order you choose. Moving a
  goal up moves the money with it, right away.
- **Every goal shows where it stands.** Not started, Saving, or Funded, with how
  much is there and how much is still to go. When you buy it, mark it as bought:
  it moves to "Completed this year" and its account is free for what's next.
- **Export your data, and bring it to a new laptop.** "Export your data" at the
  bottom of the sidebar saves everything in one file, without your bank login.
  On a fresh install, "Bring in your export" on the first screen puts it all
  back.
- **Budgets that repeat every few months.** A budget can cover every 1, 2, 3,
  4, 6 or 12 months, following the calendar (every 3 months is Jan–Mar,
  Apr–Jun, …). Each round starts fresh, and Categories shows what each one
  closed at this year: "Jan–Mar +$80.00".
- **One-time or recurring amounts.** A goal can be an amount by a date ("$500 by
  December 20") or an amount every 1, 2, 3, 4, 6 or 12 months.

### Changed

- **Accounts are grouped by what they do:** checking, savings, goal accounts,
  then cards. Each account's menu says what each job means. An account that
  holds goals keeps its job until its goals move, and the menu says so.
- This update changes how your database is laid out, so BlitZave saves a safety
  copy in `~/BlitZave/backups/` first. Budgets you already set carry over as
  monthly amounts.

## 0.1.2 (unreleased)

### New

- **Card payments, transfers and refunds link themselves.** Each sync looks for
  the other side of every new transaction. A card payment and the matching
  payment received, a transfer between your own accounts, or a refund that
  exactly cancels a charge are linked when the match is clear, so they stop
  counting as spending. Anything that is unclear is left alone for you.
- **See what a linked transaction is linked to.** Open a linked row to see its
  other side (date, account, description, amount) and whether the sync or you
  linked it.
- **The "new version is ready" line says what changed.** It now links to that
  version's release notes, so you can read what's new before you update.
- **A safety copy before every update that changes your data.** If an update
  needs to change how your database is laid out, BlitZave first saves a copy in
  `~/BlitZave/backups/` (the last three are kept). If the copy fails, nothing
  is changed.

### Changed

- **Refunds and credits now live with transfers.** Adjustments shows one list,
  and each pair is named Transfer, Refund or Credit. Refunds you linked by hand
  carry over. A partial refund link (a credit smaller than its charge) is not
  supported for now and is dropped; no total changes, because both rows were
  already counted. Linked rows don't take a category.
- **Unlinking sticks.** A pair you unlink is never linked automatically again.
- **Links are made within 2 days.** The two sides of a pair must post at most 2
  days apart. A pair that posts later (a Friday card payment that lands the
  following Tuesday) is left for you to link by hand under **Find its other
  side**. This is a deliberate trade: a wrong link hides real spending, a missed
  one only waits for you.
- **Same-amount links only on credit cards.** In checking and savings, two rows
  of equal and opposite size (cash out, then a deposit of the same amount) are
  no longer assumed to belong together. A card credit that names no merchant
  still links to its charge.
- The app can no longer change your database's layout while running. Only an
  update can.
- The browser tab no longer shows an icon, just the name.

### Good to know

- The first start after updating runs a one-time database change (after the
  safety copy above).
- If you sync an old month on its own, a pair near the edge of that month can
  be linked without seeing a look-alike from the month before. Syncing months
  oldest first avoids it. It is a known limit of the small window; fixing it
  properly is on the to-do list.
- For anyone scripting against the local API: `/api/refunds` and
  `/api/transactions/{id}/refund-candidates` are gone. Use `/api/transfers`
  (`?kind=transfer|refund|credit`). `group_refunds` on the transactions list is
  now `group_pairs`.

## 0.1.1 (2026-10-31)

### Fixed

- The daily update check failed quietly in 0.1.0, because the project moved to a
  new address just before that release. It now asks the right place, so the
  "new version" line can appear.

## 0.1.0 (2026-10-31)

The first release.

- **Connect your bank, read-only.** Bring your own SimpleFIN account and paste its
  setup token once. The token is kept in your operating system's keychain, never
  in the database or logs.
- **Sync when you open it.** BlitZave syncs on every start, so the latest is
  waiting. **Sync now** pulls more any time, and a month older than your first
  sync can be pulled on its own. The first sync reaches back 90 days.
- **Accounts.** Checking, savings and credit cards. BlitZave guesses each
  account's type; your correction survives every later sync. Investment and loan
  accounts are skipped for now.
- **Transactions.** A month-by-month ledger with search, sorting, filters and
  paging. The bank's own record is never editable.
- **Categories.** Make your own, split a transaction between several, add tags,
  and choose *All transactions like this* to teach BlitZave a merchant, now and
  in every future sync.
- **Monthly budgets.** Set a budget per category and see your pace through the
  month, net of credits, with no alarms.
- **Link adjustments by hand.** For a card payment, a transfer between your own
  accounts, or a refund, choose **Find its other side**. Review everything
  linked under **Adjustments** and unlink any of it.
- **Yours and local.** Your data is one file in `~/BlitZave/`. The server listens
  only on your own machine (127.0.0.1).
- **Install from GitHub** with `uv tool install git+https://github.com/blitzave/blitzave`;
  the app opens in your browser.
- **An update check, off until you say yes.** At most once a day it asks GitHub
  for the latest release and sends nothing else. When a newer version exists it
  shows one calm line with the upgrade command.
