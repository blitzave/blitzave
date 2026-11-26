# Changelog

What changed in each BlitZave version, in plain words. Newest first.

To update: `uv tool upgrade blitzave`, then start `blitzave` as usual.

## 0.2.1 (2026-11-26)

### New

- **Ask Goose about your money.** Press **Ask Goose about your money** on Home
  and ask in your own words: "what did I spend on dining since August?", "tag
  the Portland charges as PNW trip", "how is groceries trending?". Goose shows
  the rows, sums a category or a tag by month, or proposes a change you can
  look over first. Nothing changes until you press **Apply**, and **Undo**
  takes it back. Goose runs on a model on your laptop through Ollama, or on
  Claude if you choose it and add a key.
- **What Goose sends, and what it never does.** A model gets your question,
  your category and tag names, and your accounts as aliases like `A1
  (checking)`. It never gets a transaction, an amount, a balance, a merchant,
  a bank or an account number. BlitZave plans with the model and works out
  every figure itself. Before you save a model that isn't on your laptop,
  BlitZave lists what each question would send, and nothing goes until you
  agree. A test on every change records each request Goose makes and fails if
  any of your data is in it.
- **Try it before you connect anything.** `blitzave --demo` opens BlitZave on a
  made-up household at 127.0.0.1:8791: a few months of spending, budgets, goals,
  and Goose to ask about it. One line runs it without installing:
  `uvx --from git+https://github.com/blitzave/blitzave blitzave --demo`. Nothing
  is kept: `Ctrl+C` deletes it all, and it never touches your real data, your
  keychain or your bank. It runs beside a real BlitZave, on its own port.
- **Demo and Live, side by side.** Until you connect a bank, plain `blitzave`
  opens on Demo, the same made-up household, with one calm line at the top
  saying so. It starts over every time you open it, so try anything. Choose
  **connect your bank**, paste your SimpleFIN token, and Live, your own money,
  opens with no restart. Bringing in an export from another laptop works the
  same way. Demo is never deleted: it stays one switch away. If you already
  use BlitZave, it opens on your money as before.
- **Runs where there's no keychain.** On a Linux server or WSL, where no
  keychain answers, BlitZave keeps your bank link and any API key in
  `~/BlitZave/credentials.json`, readable by you alone. It's never exported or
  backed up. Where a keychain answers, nothing changes.
- **Settings.** A row above the sidebar's footer shows which one you're in and
  opens Settings: the Demo | Live switch, and Goose's model in one place. Pick
  a provider, then a model from a list (what your Ollama has, or Claude's
  models), or type another. An API key locks once saved; **Reset** opens it
  again. Goose's drawer sends you here to choose.

### Good to know

- Sync is greyed out in Demo: it has no bank. Live still syncs when you open
  BlitZave.
- Goose's model is set once for both. It moves out of the database into
  `~/BlitZave/settings.json` the first time you start this version.
- In `blitzave --demo`, a Claude API key you give Goose is kept in memory and
  forgotten when you stop it. Goose's settings say so.
- **The guide is one click away.** A **Guide** link at the bottom of the
  sidebar opens it in a new tab. Nothing goes online until you click it.
- **Safer on your own laptop.** No other website you visit can read or change
  your BlitZave data, or show BlitZave inside its own page. Open BlitZave at
  `127.0.0.1` or `localhost`: any other address is turned away. A setup token
  only ever connects over https, and an error from it no longer repeats what
  the server said. New files BlitZave makes are readable by you alone.
- **Importing an export no longer brings Goose's model settings along.** Choose
  your model again after you move to a new laptop. Your Claude API key only
  ever goes to Anthropic.
- **For contributors: a way to see how well Goose plans.** `evals/run.py` asks
  a real model (Ollama or Claude) a set of made-up questions and scores each
  plan; `--report` and `--compare` line runs up, and `evals/charts.py` draws
  them. `BLITZAVE_DEV=1` keeps a trace of each Goose question in
  `~/BlitZave/traces/`. Both are off for everyone else: nothing is written and
  nothing ships. Ollama models that think, like qwen3, are now asked not to,
  so Goose answers faster.

## 0.1.3 (2026-11-08)

### New

- **Home: the one thing worth knowing today.** BlitZave now opens on a calm ring
  that shows how far through the month you are, with one sentence inside it:
  savings went first, a goal is a quarter, halfway or three-quarters of the
  way there, almost there or done, a heavier start (that happens), or nothing
  that needs you. A goal's milestones get a small star. Below it are what you've spent so far,
  what you've set aside, and your goals underway.
- **When each goal gets there.** Every goal on Savings shows when it arrives at
  your recent pace, as a range of months, and what a month would take to make
  its date.
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
  December 20") or an amount every 1, 2, 3, 4, 6 or 12 months. A one-time goal
  can also have no date at all, like an emergency fund you always want full.
- **"Was that the PS5?"** When money leaves a goal account and the amount is
  close to a funded goal's (within 10%, so tax or a sale still counts), Savings
  asks once whether that was the goal. Yes marks it as bought.

### Changed

- **Goals come first on Categories,** then expenses, with a link to Savings.
- **Onboarding ends with an optional first goal.**
- **Accounts are grouped by what they do:** checking, savings, goal accounts,
  then cards. Each account's menu says what each job means. An account that
  holds goals keeps its job until its goals move, and the menu says so.
- This update changes how your database is laid out, so BlitZave saves a safety
  copy in `~/BlitZave/backups/` first. Budgets you already set carry over as
  monthly amounts.

## 0.1.2 (2026-11-07)

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
