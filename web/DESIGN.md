# BlitZave web — design system

For page agents building on top of the scaffold. Read this before writing CSS —
almost everything you need already exists in `tokens.css` / `base.css`.
Full product rules live in `CLAUDE.md`; this file is just "how to build the screen."

## Look & feel in one paragraph

Dark-only, calm-luxury. Near-black graphite surfaces (never pure black), one
accent color used sparingly, hairline borders instead of shadows, generous
whitespace, large calm tabular numerals. Nothing animates on page load — motion
only answers an action (a hover, a click, a save). If a screen feels busy, remove
something; don't add a container to organize the busyness.

## Tokens (`css/tokens.css`)

Everything is a CSS custom property on `:root`. Never hardcode a hex value or a
px spacing in a page's CSS — reach for the token.

| Group | Tokens | Use for |
|---|---|---|
| Surfaces | `--bg-page`, `--bg-raised`, `--bg-well`, `--bg-lift` | page → card → input/hover → pressed/selected, in that order |
| Hairlines | `--line-faint`, `--line-strong`, `--line-accent` | borders/dividers do the work shadows would elsewhere |
| Text | `--text-primary`, `--text-secondary`, `--text-faint` | headings/numbers → labels/body → timestamps/placeholders |
| Accent | `--accent`, `--accent-hover`, `--accent-press`, `--accent-ink`, `--accent-soft`, `--accent-soft-hover` | **one accent, glacial mint.** Primary actions, active nav, focus rings. If a screen has it in more than ~2 places, remove one. |
| Status | `--danger` / `--danger-soft`, `--success` / `--success-soft` | **real errors / confirmed actions only.** Never for spending, negative balances, or any ordinary money figure — that's the Prime Directive, not a style note. |
| Type | `--font-ui`, `--text-xs` … `--text-display`, `--weight-*`, `--leading-*` | one family, system stack. `--text-display` is reserved for the one number a page leads with. |
| Numerals | `--numeric`, `--tracking-numeral` | pair these two (or just use `.u-tabular`) on every figure — money, counts, percentages |
| Space | `--space-1` … `--space-8` | 4px base scale. No arbitrary px margins. |
| Radii | `--radius-card` (16px), `--radius-control` (10px), `--radius-chip` (pill) | cards vs. controls vs. pills — the tiering is deliberate, don't flatten it to one value |
| Motion | `--ease-out`, `--t-fast/base/slow` | 150–250ms, ease-out, always |
| Elevation | `--shadow-float` | **floating overlays only** (toasts, menus). Cards use a hairline, never a shadow. |

## Component classes (`css/base.css`)

Build with these before writing new CSS. If you need a variant that isn't here,
add it narrowly (`.btn-danger`-style modifier) rather than a one-off class.

- **`.btn`** + `.btn-primary` / `.btn-ghost` / `.btn-danger` / `.btn-quiet` — the
  four button weights, light to loud: quiet text action → ghost/secondary →
  primary (mint fill, one per screen, typically) → danger (destructive, e.g.
  delete category).
- **`.input`**, **`.select`** — form controls. Wrap in **`.field`** with a
  **`.field-label`** and optional **`.field-hint`** / **`.field-error`**.
- **`.card`** — the base surface for grouped content. Add `.card-interactive`
  if the whole card is clickable (hover lifts to `--bg-lift`). `.card-title`,
  `.card-subtitle`, and `.card-figure` (the big tabular number) are sub-parts.
- **`.chip`** — small pill for a tag or filter; `.chip-accent` for a
  selected/active state; `.chip-remove` for the trailing × on a removable chip.
- **`.badge`** — small status label (account type, category kind). Default,
  `.badge-accent`, `.badge-success`, `.badge-danger`. **Sentence case, not
  ALL CAPS** — keep badge text exactly like any other label.
- **`.table`** — put it on a `<table>`. `th.is-sortable` for clickable headers,
  `.is-sorted` / `.is-sorted-desc` for the active sort indicator, `.is-numeric`
  on any `th`/`td` that holds a right-aligned, tabular-num figure.
- **`.empty-state`** (+ `.empty-state-title`, `.empty-state-body`) — the
  no-data / not-built-yet state. Every stub page already uses this.
- **`.skeleton`** (+ `.skeleton-text`, stack with `.skeleton-line`) — loading
  shimmer. Size it with width/height on the element itself; the shimmer is
  automatic.
- **`.toast`** — the sidebar's sync toast already uses this; `main.js` doesn't
  currently export a `showToast()` helper, so if a page needs its own toast,
  copy the few lines from `main.js` rather than inventing new markup. (If that
  comes up more than once, that's the signal to export it — ask first, per
  CLAUDE.md.)
- **`.link`** — an inline text link inside copy (not a nav item, not a button).
- **`.u-tabular`** — utility: wrap any numeral-bearing text in this (or apply
  `font-variant-numeric: var(--numeric)` directly) so digit columns don't jitter.

## Page & component contract (restated)

Every page module in `js/pages/*.js` exports exactly:

```js
export function render(root, params) { … }
```

`root` is an `HTMLElement` — own it entirely (`root.innerHTML = …` or build DOM
into it). `params` is whatever the router parsed from the URL (e.g. `{id}` on
`#/accounts/:id`). Fetch your own data via `js/api.js`; don't call `fetch()`
directly.

The shared transactions table lives in `js/components/transactions-table.js`:

```js
export function renderTransactionsTable(root, { accountId = null } = {}) {}
```

Self-fetching: sort controls, filters, pagination, per-row metadata editing.
When `accountId` is passed, it scopes to that account and hides the account
filter — the accounts page's detail view is expected to call this instead of
building its own table.

**Routes** (owned by `js/router.js`, don't edit): `#/onboarding`,
`#/accounts` (also the default, `#/` → `/accounts`), `#/accounts/:id`,
`#/transactions`, `#/categories`.

**Nav-link highlighting** is automatic: any element with `data-nav-link` and an
`href="#/section"` gets `.is-active` toggled by the router based on the current
path (prefix match, so `#/accounts/ACT-1` still lights up the `#/accounts`
link). You don't need to wire this yourself.

**Onboarding handoff:** once setup is done, `onboarding.js` must run, in order:

```js
localStorage.setItem('bz.onboarded', '1');
window.__bz.refreshShell();
```

`refreshShell()` un-hides the sidebar and navigates to `/accounts`. Skipping
the `localStorage` write means the next reload sends the user back through
onboarding.

**Money on screen:** every money figure you render must be a `*_display` string
straight from the API response (`amount_display`, `balance_display`, etc.).
Raw cents are fine to use for geometry (a bar's width proportion) — never for
anything a user reads as a number.

## Voice — five lines

BlitZave is an ally, never a scorekeeper.

1. Report behavior, never judge it: "Dining ran higher this month," not "You
   overspent on dining again."
2. A bad month gets absorbed, not punished — point at the longer trend and one
   next move, not the miss.
3. Calm, neutral-to-warm, plain sentence case. No streaks, no grades, no guilt.
4. Red (`--danger`) means a real failure — a request that errored, a sync that
   couldn't complete. It is never the color of a number the user didn't like.
5. If a string of copy reads like a parent or a scoreboard, rewrite it as
   something a good friend who's good with money would actually say.
