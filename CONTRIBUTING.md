# Contributing to BlitZave

Thanks for being here. BlitZave is a personal finance app that runs on your own
laptop and **has your back**: it reflects what you're doing with money, calmly,
and never keeps score. Every contribution, from a typo fix to a new detector,
helps more people feel in control of their money.

- **Chat first** on [Discord](https://discord.com/invite/VerUYP67) if you're
  unsure whether an idea fits. A five-minute conversation saves a rewritten PR.
- **Small PRs win.** One focused change, with tests, that a reviewer can read
  end to end.

---

## The rule above all others: ally, never scorekeeper

Before you ship anything, ask:

> **Does this behave like an ally, or like a scorekeeper?**

| Situation | Scorekeeper (not accepted) | Ally (what we ship) |
|---|---|---|
| A bad month | "Streak broken. You missed your goal." | "Rough month. Happens. Your 3-month average is still up." |
| Overspending | "You overspent on dining again." | "Dining ran higher this month. Want to see what shifted?" |
| A win | (silence, or a hollow badge) | "Your no-spend mornings held. That's $180 toward your emergency fund." |

BlitZave **reports behavior; it never scolds it.** Any copy, color, or mechanic
that turns a dip into a failure state is a bug, even if it's technically
accurate. In practice:

- **Red means a real failure only**: a request that errored, a sync that
  couldn't finish. Never a number the user didn't like.
- **No streaks, grades, or guilt.** Plain sentence case. Calm, neutral to warm.
- **Calm on top, power underneath.** The default view is sparse; the detail
  lives one tap down.

## Lines we don't cross

- **Bank access is read-only, always.** BlitZave can never move money.
- **Tracking, not management.** We track savings and investments; we never
  advise on them.
- **No invented numbers.** Every figure a user sees comes from deterministic
  code over their own data.
- **Local-first.** The server binds to `127.0.0.1` only. The only network call
  is the user's own bank sync. No telemetry, no analytics, no remote fonts or
  CDNs in the UI.
- **Secrets live in the OS keychain**, never in the database, logs, exports,
  issues, or screenshots.

**Not now (on purpose):** hosting and user accounts, payments, native or mobile
apps, proactive notifications, usage metrics, and gamification. If your idea
needs one of these, open a discussion first. It's likely a "later," not a "no."

---

## How the code is organized

```
src/blitzave/
├── __main__.py   # starts the server on 127.0.0.1:8742; syncs on startup
├── sources/      # SimpleFIN fetch + normalize
├── store/        # SQLite access and numbered migrations
├── refine/       # pure analysis: categorize, budgets, (soon) behavior detectors
├── api/          # FastAPI routes and money parsing/formatting — the contract
└── jobs.py       # the sync pipeline
web/              # the UI: plain HTML/CSS/JS modules, served at /
tests/            # pytest; synthetic fixtures only
```

There are two zones, and the API is the wall between them:

| Zone | Where | How we build it |
|---|---|---|
| **Engine** | `src/blitzave/` | Small, reviewed steps. Tested. You can explain every line. |
| **UI** | `web/` | Moves faster, but may only talk to `/api/*` over HTTP. It never imports engine code and never does money math. It shows the API's `*_display` strings. |

Never add CORS middleware. The server has no auth, so permissive CORS would let
any website the user visits read their financial data. Same-origin serving is
why the UI lives inside the engine.

## Money and data rules

These are non-negotiable, and review enforces them:

- **Money is integer cents** (`amount_cents INTEGER` in `STRICT` tables). Never floats.
- **Parse with `api/money.py`.** `parse_amount()` builds cents with exact integer
  arithmetic and fails loudly. Never round silently.
- **Every money field goes out as a pair:** `amount_cents` and `amount_display`.
- **Sign convention:** negative = money left the account, positive = it arrived.
- **Count every dollar once.** Analysis reads the `activity` view, never raw
  `transactions`, so a card payment is never counted as spending.
- **A sync never changes an existing transaction's amount**, and ingestion is
  idempotent: re-syncing never creates duplicates.
- **User corrections always win** over rules.
- **Schema changes are new numbered migrations** in `store/migrations/`. Never
  edit one that has shipped.
- **Every write goes through a Pydantic-validated endpoint.** Bad input gets a
  422; it is never silently "fixed up."

## Development setup

You need [uv](https://docs.astral.sh/uv/). It installs Python 3.12 for you if
needed.

```bash
git clone https://github.com/blitzave/engine.git blitzave
cd blitzave
uv sync                  # install, including dev tools
uv run blitzave          # http://127.0.0.1:8742 (API docs at /docs)
uv run pytest            # tests
uv run ruff check .      # lint
```

Point `BLITZAVE_HOME` at a scratch folder to keep development data away from
your real `~/BlitZave`:

```bash
BLITZAVE_HOME=/tmp/bz-dev uv run blitzave
```

**Never commit real financial data.** Fixtures, issues, screenshots and logs
use synthetic or scrubbed data only.

## Pull request checklist

- [ ] The change is small and focused, and the PR says *why*.
- [ ] Engine changes have tests; `uv run pytest` and `uv run ruff check .` pass.
- [ ] Any user-facing copy passes the ally-not-scorekeeper test.
- [ ] No new network calls, telemetry, or remote assets.
- [ ] No real financial data anywhere in the diff.
- [ ] New dependencies are justified in the PR description. Fewer moving parts wins.

## Contributor License Agreement

Before we can merge your first pull request, we ask you to sign our
[Contributor License Agreement](CLA.md). It's a one-time, one-click step. You
keep the copyright to your work. The CLA lets the project accept your
contribution and keep options open as BlitZave grows, while the core stays
free and open source under Apache-2.0.

## Code of conduct

Everyone participating in BlitZave is expected to follow our
[Code of Conduct](CODE_OF_CONDUCT.md). In short: be kind, assume good faith,
and remember that money is personal.
