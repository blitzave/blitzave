# BlitZave web — design system

For page agents building on top of the scaffold. Read this before writing CSS —
almost everything you need already exists in `tokens.css` / `base.css`.
Full product rules live in `CLAUDE.md`; this file is just "how to build the screen."

## Look & feel in one paragraph

"Daylight, Apple-grade": the same calm-light, ally-not-scorekeeper voice as before,
finished to the standard of Apple Health / Wallet light mode. A neutral Apple-grey
page (`#F5F5F7`), true-white cards that **float** on a layered soft shadow (depth is
real now, not a hairline standing in for it), deep ink, and ONE deep-indigo accent
used only for action (buttons, active nav, focus, links). Money figures are **bold
sans, tight-tracked, tabular** — the system serif is retired from numerals entirely
and lives in exactly one place in the whole app: the BlitZave wordmark in the
sidebar, an editorial signature, not a numeral face. Headings are bold (700) with
tight tracking, sized up from before so a page title actually reads as a page title.
Iconography is real inline SVG (16–20px, round-cap strokes, currentColor) — no text
glyphs standing in for a chevron or an × anymore. Status color is still
un-moralistic: pine = on-track/confirmed, ochre = a neutral heads-up, coral = a real
failure only — never the color of a number the user didn't like. Nothing animates on
page load — motion only answers an action. One signature moment per screen,
restraint everywhere else: if a screen feels busy, remove something; don't add a
container to organize the busyness.

**The five rules that define "premium" here — hold every screen to all five:**

1. **Depth.** Cards float on `--shadow-card` (a tight contact shadow + a soft
   diffuse one) plus a near-invisible border. A flat card with just a hairline
   reads as a wireframe — that's the thing we killed.
2. **Type hierarchy.** Headings bold (700), tight tracking (`--tracking-tight`,
   -0.02em), sized to lead (`--text-title`, 34px). Money is bold sans, tight,
   tabular — never the serif.
3. **Clean neutrals.** Apple-grey page, true-white cards, Apple-ink text tiers.
   The indigo accent and the pine/ochre/coral status trio are unchanged — don't
   drift them.
4. **Real iconography.** Crisp inline SVG (16–20px, 1.5–1.75 stroke, currentColor,
   round caps) wherever the UI used to lean on a text character (`⋯ ‹ › ×`). No
   icon fonts, no external assets — everything is inline markup.
5. **One signature moment per screen.** Restraint everywhere else. A page earns
   one hero figure, one standout card — not five things all shouting at once.

(History: this replaced an earlier "Daylight" skin that used a green-tinted
porcelain page and serif numerals — both read as murky/spindly and are the reason
this pass exists. Before that: a dark graphite+mint skin, generic dark-fintech; and
earth-brown/sienna, rejected earlier still. Don't reintroduce any of the three.)

## Tokens (`css/tokens.css`)

Everything is a CSS custom property on `:root`. Never hardcode a hex value or a
px spacing in a page's CSS — reach for the token.

| Group | Tokens | Use for |
|---|---|---|
| Surfaces | `--bg-page` (#F5F5F7), `--bg-raised` (#FFFFFF), `--bg-well` (#EFEFF1), `--bg-lift` (#E5E5EA) | page → card → input/hover → pressed/selected, in that order |
| Hairlines | `--line-faint`, `--line-strong`, `--line-accent` | now near-invisible finishing edges — shadows carry the depth, borders just crisp the edge |
| Text | `--text-primary` (#1D1D1F), `--text-secondary` (#6E6E73), `--text-faint` (#AEAEB2) | headings/numbers → labels/body → timestamps/placeholders |
| Accent | `--accent`, `--accent-rgb`, `--accent-hover`, `--accent-press`, `--accent-ink`, `--accent-soft`, `--accent-soft-hover` | **one accent, deep indigo — for ACTION only.** Primary actions, active nav, focus rings, inline links, the sort arrow. Not money, not status. `--accent-rgb` is the raw channel triple, for building one-off accent-tinted shadows without a new hex. |
| Status | `--success`/`-soft` (pine), `--warn`/`-soft` (ochre), `--danger`/`-soft` (coral) | **state & system only.** success = confirmed/on-track, warn = neutral heads-up ("worth a look"), danger = a real failure (errored request, failed sync). Never spending, never a balance — Prime Directive, not a style note. |
| Type | `--font-ui` (SF Pro — interface **and all numerals now**) + `--font-display` (system serif, "New York" — **wordmark only**), `--text-xs` … `--text-title`/`--text-hero`, `--weight-*` (incl. new `--weight-bold`), `--leading-*` | sans for everything including figures; serif is retired everywhere except the sidebar wordmark |
| Numerals | `--numeric`, `--tracking-numeral` (-0.01em, in-flow numerals like table cells), `--tracking-tight` (-0.02em, headings + hero/display figures) | pair the right tracking token with any numeral-bearing text, or just use `.u-tabular` for the moderate (table) case |
| Space | `--space-1` … `--space-8` | 4px base scale. No arbitrary px margins. |
| Radii | `--radius-card` (20px), `--radius-control` (12px), `--radius-chip` (pill — also used for nav items now) | cards vs. controls vs. pills — the tiering is deliberate, don't flatten it to one value |
| Motion | `--ease-out`, `--t-fast/base/slow` | 150–250ms, ease-out, always |
| Elevation | `--shadow-card`, `--shadow-card-hover`, `--shadow-float`, `--shadow-accent` | **cards use `--shadow-card` now** (resting) and `--shadow-card-hover` (lifted) — the old "cards never get a shadow" rule is retired. `--shadow-float` is still for floating overlays (toasts, menus, popovers). `--shadow-accent` is the subtle colored lift under the one filled indigo primary button. |
| Layout | `--blur-nav` | saturate+blur recipe for a frosted surface (the sidebar uses it, guarded by `@supports`) |

### Renamed / new since the last pass — read this if you built against the old tokens

- `--bg-page` is now `#F5F5F7` (was the green-tinted `#f1f3ef`). `--bg-well` /
  `--bg-lift` moved to match — same roles, new neutral values.
- `--line-faint` / `--line-strong` are now ink-at-low-alpha on **black**, not the
  old green-ink mix, and are much lower alpha (borders do less work now that
  shadows exist).
- `--text-xl` is now **34px** (was 30px) — this is the page-heading size. New
  alias `--text-title` is the same 34px value, named for clarity.
- `--text-hero` is now **56px** (was 64px) — bold sans reads bigger than the old
  serif did at the same size, so the px came down to compensate.
- `--text-display` is now **36px** (was 44px), same reasoning, for `.card-figure`.
- New: `--weight-bold` (700), `--tracking-tight` (-0.02em), `--shadow-card`,
  `--shadow-card-hover`, `--shadow-accent`, `--accent-rgb`, `--blur-nav`.
- `--radius-card` is now **20px** (was 16px), `--radius-control` is now **12px**
  (was 10px).
- `--sidebar-width` is now **240px** (was 232px) — room for the new nav icons.
- `.card-figure` and `.figure-hero` dropped `--font-display` (serif) entirely —
  they're `--font-ui`, `--weight-bold`, `--tracking-tight` now. If a page hardcoded
  `font-family: var(--font-display)` directly on a money figure instead of using
  these classes, switch it over.

## Component classes (`css/base.css`)

Build with these before writing new CSS. If you need a variant that isn't here,
add it narrowly (`.btn-danger`-style modifier) rather than a one-off class.

- **`.btn`** (44px min-height target now) + `.btn-primary` / `.btn-ghost` /
  `.btn-danger` / `.btn-quiet` — the four button weights, light to loud: quiet text
  action → ghost/secondary → primary (indigo fill, subtle colored shadow, presses
  with a `scale(.98)`) → danger (destructive, e.g. delete category).
- **`.input`**, **`.select`** — form controls, 44px tall, inset well background.
  Wrap in **`.field`** with a **`.field-label`** and optional **`.field-hint`** /
  **`.field-error`**.
- **`.card`** — the base surface for grouped content; floats on `--shadow-card` by
  default now. Add `.card-interactive` if the whole card is clickable (hover lifts
  further via `--shadow-card-hover` + a 2px rise, no fill change). `.card-title`,
  `.card-subtitle`, and `.card-figure` (the big bold-sans tabular number) are
  sub-parts.
- **`.chip`** — small pill for a tag or filter; `.chip-accent` for a
  selected/active state. **`.chip-remove`** is now a flex container meant to hold a
  real inline `<svg class="chip-remove-icon">` (an × built from two crossed
  strokes) instead of a text "×" character — see the comment above `.chip-remove`
  in `base.css` for the exact markup shape.
- **`.badge`** — small status label (account type, category kind). Default,
  `.badge-accent`, `.badge-success`, `.badge-warn` (new — ochre, for a neutral
  heads-up badge), `.badge-danger`. **Sentence case, not ALL CAPS** — keep badge
  text exactly like any other label.
- **`.table`** — put it on a `<table>`. Rows are roomier now (16px cell padding).
  `th.is-sortable` for clickable headers, `.is-sorted` / `.is-sorted-desc` for the
  active sort indicator (a crisp 2px accent chevron), `.is-numeric` on any `th`/`td`
  that holds a right-aligned, tabular-num figure — numeric `td`s are semibold by
  default now, so money carries real weight against the row.
- **`.empty-state`** (+ `.empty-state-title`, `.empty-state-body`) — the
  no-data / not-built-yet state. Every stub page already uses this.
- **`.skeleton`** (+ `.skeleton-text`, stack with `.skeleton-line`) — loading
  shimmer. Size it with width/height on the element itself; the shimmer is
  automatic.
- **`.toast`** — the sidebar's sync toast already uses this; it now floats on
  `--shadow-float` with only a hairline edge, not a heavy border. `main.js`
  doesn't currently export a `showToast()` helper, so if a page needs its own
  toast, copy the few lines from `main.js` rather than inventing new markup. (If
  that comes up more than once, that's the signal to export it — ask first, per
  CLAUDE.md.)
- **`.link`** — an inline text link inside copy (not a nav item, not a button).
- **`.u-tabular`** — utility: wrap any numeral-bearing text in this (or apply
  `font-variant-numeric: var(--numeric)` directly) so digit columns don't jitter.
  Uses the moderate `--tracking-numeral`; for a big hero/display figure, use
  `.figure-hero` / `.card-figure` instead so you get `--tracking-tight`.

### Iconography — the rule, not just the sidebar

Any text glyph standing in for an icon (`⋯ ‹ › ×`, an arrow built from `>`, etc.)
is now in scope to replace with a real inline SVG: 16–20px, 1.5–1.75 stroke,
`stroke="currentColor"`, round caps/joins, `fill="none"` unless it's a small solid
dot/check. No icon fonts, no external sprite sheets, no remote requests — every
icon is markup written straight into the page's JS (see `main.js`'s `ICON_*`
constants for the pattern: a small SVG string constant, inlined into the template).
The one exception already in `base.css`: the table's sort chevron, which is a
border-corner trick, not a glyph — that one's fine to leave as CSS, no SVG needed.

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
anything a user reads as a number. And per the type-hierarchy rule above: render
that figure through `.card-figure` / `.figure-hero`, or at minimum `font-weight:
var(--weight-bold)` + `.u-tabular` — never through `--font-display`.

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
