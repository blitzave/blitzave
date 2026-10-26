// BlitZave — categories page.
// Monthly expense budgets above "Your categories" management.
//
// Money rule: every figure on screen is a *_display string straight from
// the API. Budget inputs remain strings for the API to parse and validate.

import * as api from "../api.js";

const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

const KIND_ORDER = ["expense", "income", "savings"];
const KIND_LABELS = { expense: "Expense", income: "Income", savings: "Savings" };

// How many muted-but-distinct data hues the tracker bars rotate through —
// defined as --cat-hue-0..4 in categories.css, scoped to .categories-page.
// Ochre/coral are reserved for status and never used here.
const DATA_HUE_COUNT = 5;

// Real inline SVG, 16-20px, currentColor, round caps — see DESIGN.md's
// iconography rule. No text glyphs standing in for a chevron, dots, or a
// plus anymore.
const ICON_CHEVRON_LEFT = `
  <svg class="month-nav-icon" viewBox="0 0 24 24" aria-hidden="true">
    <path d="M15 5l-7 7 7 7" />
  </svg>
`;

const ICON_CHEVRON_RIGHT = `
  <svg class="month-nav-icon" viewBox="0 0 24 24" aria-hidden="true">
    <path d="M9 5l7 7-7 7" />
  </svg>
`;

const ICON_CHEVRON_DOWN = `
  <svg class="quiet-categories-icon" viewBox="0 0 24 24" aria-hidden="true">
    <path d="M6 9l6 6 6-6" />
  </svg>
`;

const ICON_ELLIPSIS = `
  <svg class="category-menu-icon" viewBox="0 0 24 24" aria-hidden="true">
    <circle cx="5" cy="12" r="1.8" fill="currentColor" stroke="none" />
    <circle cx="12" cy="12" r="1.8" fill="currentColor" stroke="none" />
    <circle cx="19" cy="12" r="1.8" fill="currentColor" stroke="none" />
  </svg>
`;

const ICON_PLUS = `
  <svg class="category-add-icon" viewBox="0 0 24 24" aria-hidden="true">
    <path d="M12 5v14M5 12h14" />
  </svg>
`;

// ----------------------------------------------------------------------
// Module state. `root` is the same DOM node across every visit to this
// page (the router clears its innerHTML but never replaces the element),
// so listeners are wired once onto it and state is rebuilt fresh on each
// render() call. `token` guards against a late-resolving request from a
// visit the user has already navigated away from writing stale markup
// into whatever page is on screen now.
// ----------------------------------------------------------------------

let rootEl = null;
let state = null;
let token = 0;
let summarySeq = 0;

function freshState() {
  const today = new Date();
  return {
    year: today.getFullYear(),
    month: today.getMonth(), // 0-indexed
    categories: null,
    summary: null,
    loadingCategories: true,
    loadingSummary: true,
    categoriesError: null,
    summaryError: null,
    editingNameId: null,
    editingKindId: null,
    confirmDeleteId: null,
    kindSaving: null,
    accounts: [],
    accountsError: null,
    activity: {},
    editingBudgetId: null,
    budgetDrafts: {},
    budgetErrors: {},
    budgetSaving: null,
    pendingNameValue: {},
    rowErrors: {},
    showNewForm: false,
    newForm: { name: "", kind: "expense", error: null, saving: false },
  };
}

function stale(myToken) {
  return myToken !== token || !rootEl.querySelector(".categories-page");
}

export function render(root) {
  rootEl = root;
  state = freshState();
  token += 1;
  const myToken = token;
  wireEvents(root);
  mount(myToken);
}

// ------------------------------- Data flow -------------------------------

function monthRange(year, month) {
  const pad = (n) => String(n).padStart(2, "0");
  const start = `${year}-${pad(month + 1)}-01`;
  const lastDay = new Date(year, month + 1, 0).getDate();
  const end = `${year}-${pad(month + 1)}-${pad(lastDay)}`;
  return { start, end };
}

async function mount(myToken) {
  const seq = ++summarySeq;
  paint(); // initial skeleton
  const { start, end } = monthRange(state.year, state.month);
  const [categoriesResult, summaryResult, accountsResult] = await Promise.allSettled([
    api.listCategories(),
    api.categorySummary(start, end),
    api.listAccounts(),
  ]);
  if (stale(myToken)) return;

  if (categoriesResult.status === "fulfilled") {
    state.categories = categoriesResult.value;
    state.categoriesError = null;
  } else {
    state.categoriesError = categoriesResult.reason?.detail || "Couldn't load your categories.";
  }

  if (seq === summarySeq) {
    if (summaryResult.status === "fulfilled") {
      state.summary = summaryResult.value;
      state.summaryError = null;
    } else {
      state.summaryError = summaryResult.reason?.detail || "Couldn't load this month.";
    }
    state.loadingSummary = false;
  }

  if (accountsResult.status === "fulfilled") {
    state.accounts = accountsResult.value;
  } else {
    state.accountsError = "Account names couldn't load. Account identifiers are shown below.";
  }

  state.loadingCategories = false;
  paint();
}

async function loadSummaryForMonth(myToken) {
  const seq = ++summarySeq;
  state.loadingSummary = true;
  state.summaryError = null;
  paint();

  const { start, end } = monthRange(state.year, state.month);
  try {
    const summary = await api.categorySummary(start, end);
    if (stale(myToken) || seq !== summarySeq) return;
    state.summary = summary;
    state.summaryError = null;
  } catch (err) {
    if (stale(myToken) || seq !== summarySeq) return;
    state.summary = null;
    state.summaryError = err.detail || "Couldn't load this month.";
  }
  if (stale(myToken) || seq !== summarySeq) return;
  state.loadingSummary = false;
  paint();
}

async function loadCategoriesOnly(myToken) {
  state.loadingCategories = true;
  state.categoriesError = null;
  paint();
  try {
    const categories = await api.listCategories();
    if (stale(myToken)) return;
    state.categories = categories;
    state.categoriesError = null;
  } catch (err) {
    if (stale(myToken)) return;
    state.categoriesError = err.detail || "Couldn't load your categories.";
  }
  if (stale(myToken)) return;
  state.loadingCategories = false;
  paint();
}

/** Refetch both lists so the tracker and the manage section always show
 * what the server actually has — never a figure we patched together
 * ourselves. */
async function refreshCategoriesAndSummary() {
  const myToken = token;
  const seq = ++summarySeq;
  const { start, end } = monthRange(state.year, state.month);
  const [categories, summary] = await Promise.all([
    api.listCategories(),
    api.categorySummary(start, end),
  ]);
  if (stale(myToken)) return;
  state.categories = categories;
  state.categoriesError = null;
  if (seq === summarySeq) {
    state.summary = summary;
    state.summaryError = null;
    state.loadingSummary = false;
  }
}

function changeMonth(delta) {
  let month = state.month + delta;
  let year = state.year;
  if (month < 0) {
    month = 11;
    year -= 1;
  } else if (month > 11) {
    month = 0;
    year += 1;
  }
  state.month = month;
  state.year = year;
  state.activity = {};
  state.editingBudgetId = null;
  loadSummaryForMonth(token);
}

async function loadCategoryActivity(id, offset = 0) {
  const myToken = token;
  const activity = state.activity[id];
  if (!activity || activity.loading) return;
  activity.loading = true;
  activity.error = null;
  activity.requestedOffset = offset;
  paint();
  const { start, end } = monthRange(state.year, state.month);
  try {
    const result = await api.listTransactions({ category_id: id, start, end, limit: 20, offset });
    if (stale(myToken) || state.activity[id] !== activity) return;
    activity.rows = result.transactions;
    activity.total = result.total;
    activity.offset = offset;
  } catch (err) {
    if (stale(myToken) || state.activity[id] !== activity) return;
    activity.error = errorDetail(err, "Couldn't load these transactions.");
  }
  if (stale(myToken) || state.activity[id] !== activity) return;
  activity.loading = false;
  paint();
}

function toggleCategoryActivity(id) {
  let activity = state.activity[id];
  if (!activity) {
    activity = { open: true, rows: null, total: 0, offset: 0, loading: false, error: null };
    state.activity[id] = activity;
  } else {
    activity.open = !activity.open;
  }
  if (activity.open && !activity.rows && !activity.loading) {
    loadCategoryActivity(id);
  } else {
    paint();
  }
}

async function reloadAccounts() {
  const myToken = token;
  try {
    const accounts = await api.listAccounts();
    if (stale(myToken)) return;
    state.accounts = accounts;
    state.accountsError = null;
  } catch (err) {
    if (stale(myToken)) return;
    state.accountsError = errorDetail(err, "Couldn't load account names.");
  }
  paint();
}

// ------------------------------- Mutations -------------------------------

async function handleBudgetSubmit(form) {
  const id = Number(form.dataset.id);
  if (state.budgetSaving !== null) return;
  const myToken = token;
  const value = form.querySelector('[data-role="budget-input"]').value.trim();
  state.budgetDrafts[id] = value;
  state.budgetErrors[id] = null;
  state.budgetSaving = id;
  paint();
  try {
    await api.updateCategory(id, { budget_amount: value || null });
  } catch (err) {
    if (stale(myToken)) return;
    state.budgetSaving = null;
    state.budgetErrors[id] = errorDetail(err, "Couldn't save this budget. Try again.");
    paint();
    focusBudgetInput(id);
    return;
  }
  if (stale(myToken)) return;
  state.budgetSaving = null;
  state.editingBudgetId = null;
  delete state.budgetDrafts[id];
  try {
    await refreshCategoriesAndSummary();
  } catch {
    if (stale(myToken)) return;
    state.summaryError = "Your budget was saved, but this month couldn't refresh. Try loading it again.";
  }
  if (stale(myToken)) return;
  paint();
}

async function handleNameSubmit(inputEl) {
  const id = Number(inputEl.dataset.id);
  const myToken = token;

  if (inputEl.dataset.cancelled === "1") {
    state.editingNameId = null;
    delete state.pendingNameValue[id];
    paint();
    return;
  }

  const value = inputEl.value.trim();
  const original = (state.categories.find((c) => c.id === id) || {}).name || "";
  if (!value || value === original) {
    state.editingNameId = null;
    delete state.pendingNameValue[id];
    paint();
    return;
  }

  try {
    await api.updateCategory(id, { name: value });
  } catch (err) {
    if (stale(myToken)) return;
    state.pendingNameValue[id] = value;
    state.rowErrors[id] =
      err.status === 409
        ? { text: `Already have a category named "${value}."`, note: true }
        : { text: err.detail || "Couldn't save that. Try again." };
    paint();
    focusNameInput(id);
    return;
  }
  if (stale(myToken)) return;

  state.editingNameId = null;
  delete state.pendingNameValue[id];
  state.rowErrors[id] = null;
  try {
    await refreshCategoriesAndSummary();
  } catch {
    // The rename itself landed; a refresh hiccup just leaves the lists
    // stale until the next load.
  }
  if (stale(myToken)) return;
  paint();
}

function handleKindCancelIfIdle(selectEl) {
  const id = Number(selectEl.dataset.id);
  if (state.kindSaving === id) return; // handleKindChange will paint when it's done
  if (state.editingKindId === id) {
    state.editingKindId = null;
    paint();
  }
}

async function handleKindChange(selectEl) {
  const id = Number(selectEl.dataset.id);
  const newKind = selectEl.value;
  const myToken = token;
  const original = (state.categories.find((c) => c.id === id) || {}).kind;

  if (newKind === original) {
    state.editingKindId = null;
    paint();
    return;
  }

  state.kindSaving = id;
  try {
    await api.updateCategory(id, { kind: newKind });
  } catch (err) {
    if (stale(myToken)) return;
    state.kindSaving = null;
    state.editingKindId = null;
    state.rowErrors[id] = { text: err.detail || "Couldn't change that. Try again." };
    paint();
    return;
  }
  if (stale(myToken)) return;

  state.kindSaving = null;
  state.editingKindId = null;
  state.rowErrors[id] = null;
  try {
    await refreshCategoriesAndSummary();
  } catch {
    // Kind change landed; stale lists resolve on next load.
  }
  if (stale(myToken)) return;
  paint();
}

async function handleDelete(id) {
  const myToken = token;
  try {
    await api.deleteCategory(id);
  } catch (err) {
    if (stale(myToken)) return;
    state.confirmDeleteId = null;
    state.rowErrors[id] =
      err.status === 409
        ? { text: "Still in use by some transactions — recategorize them first.", link: true, note: true }
        : { text: err.detail || "Couldn't delete that. Try again." };
    paint();
    return;
  }
  if (stale(myToken)) return;

  state.confirmDeleteId = null;
  state.rowErrors[id] = null;
  try {
    await refreshCategoriesAndSummary();
  } catch {
    // Deletion landed; stale lists resolve on next load.
  }
  if (stale(myToken)) return;
  paint();
}

async function handleCreateCategory(nameInput, kindSelect) {
  const name = nameInput.value.trim();
  const kind = kindSelect.value;
  const myToken = token;

  if (!name) {
    state.newForm = { name: nameInput.value, kind, error: "Give it a name first.", note: true, saving: false };
    paint();
    focusNewCategoryName();
    return;
  }

  state.newForm = { name, kind, error: null, saving: true };
  paint();

  try {
    await api.createCategory(name, kind);
  } catch (err) {
    if (stale(myToken)) return;
    state.newForm = {
      name,
      kind,
      error:
        err.status === 409
          ? `Already have a category named "${name}."`
          : err.detail || "Couldn't add that. Try again.",
      note: err.status === 409,
      saving: false,
    };
    paint();
    focusNewCategoryName();
    return;
  }
  if (stale(myToken)) return;

  state.showNewForm = false;
  state.newForm = { name: "", kind: "expense", error: null, saving: false };
  try {
    await refreshCategoriesAndSummary();
  } catch {
    // Creation landed; stale lists resolve on next load.
  }
  if (stale(myToken)) return;
  paint();
}

// -------------------------------- Events --------------------------------

function wireEvents(root) {
  if (root.__bzCategoriesWired) return;
  root.__bzCategoriesWired = true;
  root.addEventListener("click", onClick);
  root.addEventListener("change", onChange);
  root.addEventListener("focusout", onFocusOut);
  root.addEventListener("keydown", onKeydown);
  root.addEventListener("submit", onSubmit);
  root.addEventListener("input", onInput);
}

function onInput(e) {
  if (e.target.matches('[data-role="budget-input"]')) {
    state.budgetDrafts[Number(e.target.dataset.id)] = e.target.value;
  }
}

function onClick(e) {
  const target = e.target.closest("[data-action]");
  if (!target) return;
  const action = target.dataset.action;
  const id = target.dataset.id ? Number(target.dataset.id) : null;

  switch (action) {
    case "prev-month":
      changeMonth(-1);
      break;
    case "next-month":
      changeMonth(1);
      break;
    case "retry-summary":
      loadSummaryForMonth(token);
      break;
    case "retry-categories":
      loadCategoriesOnly(token);
      break;
    case "edit-budget": {
      if (state.budgetSaving !== null) break;
      const category = state.summary.categories.find((c) => c.id === id);
      state.editingBudgetId = id;
      state.budgetDrafts[id] = category.budget_display ?? "";
      state.budgetErrors[id] = null;
      paint();
      focusBudgetInput(id);
      break;
    }
    case "cancel-budget":
      if (state.budgetSaving !== null) break;
      state.editingBudgetId = null;
      delete state.budgetDrafts[id];
      state.budgetErrors[id] = null;
      paint();
      break;
    case "toggle-activity":
      toggleCategoryActivity(id);
      break;
    case "activity-page":
      loadCategoryActivity(id, Number(target.dataset.offset));
      break;
    case "retry-accounts":
      reloadAccounts();
      break;
    case "start-rename":
      state.editingKindId = null;
      state.confirmDeleteId = null;
      state.editingNameId = id;
      delete state.pendingNameValue[id];
      state.rowErrors[id] = null;
      paint();
      focusNameInput(id);
      break;
    case "start-kind-edit":
      state.editingNameId = null;
      state.confirmDeleteId = null;
      state.editingKindId = id;
      paint();
      focusKindSelect(id);
      break;
    case "toggle-delete-confirm":
      state.editingNameId = null;
      state.editingKindId = null;
      state.confirmDeleteId = state.confirmDeleteId === id ? null : id;
      paint();
      break;
    case "cancel-delete":
      state.confirmDeleteId = null;
      paint();
      break;
    case "confirm-delete":
      handleDelete(id);
      break;
    case "new-category-toggle":
      state.showNewForm = true;
      state.newForm.error = null;
      paint();
      focusNewCategoryName();
      break;
    case "new-category-cancel":
      state.showNewForm = false;
      state.newForm = { name: "", kind: "expense", error: null, saving: false };
      paint();
      break;
    default:
      break;
  }
}

function onChange(e) {
  const kindSelect = e.target.closest('[data-role="kind-select"]');
  if (kindSelect) handleKindChange(kindSelect);
}

function onFocusOut(e) {
  const nameInput = e.target.closest('[data-role="name-input"]');
  if (nameInput) {
    handleNameSubmit(nameInput);
    return;
  }
  const kindSelect = e.target.closest('[data-role="kind-select"]');
  if (kindSelect) handleKindCancelIfIdle(kindSelect);
}

function onKeydown(e) {
  if (e.target.matches('[data-role="budget-input"]') && e.key === "Escape") {
    if (state.budgetSaving !== null) return;
    state.editingBudgetId = null;
    paint();
    return;
  }
  const nameInput = e.target.closest('[data-role="name-input"]');
  if (nameInput) {
    if (e.key === "Enter") {
      e.preventDefault();
      nameInput.blur();
    } else if (e.key === "Escape") {
      nameInput.dataset.cancelled = "1";
      nameInput.blur();
    }
    return;
  }
  const kindSelect = e.target.closest('[data-role="kind-select"]');
  if (kindSelect && e.key === "Escape") {
    state.editingKindId = null;
    paint();
  }
}

function onSubmit(e) {
  const budgetForm = e.target.closest('[data-role="budget-form"]');
  if (budgetForm) {
    e.preventDefault();
    handleBudgetSubmit(budgetForm);
    return;
  }
  const form = e.target.closest('[data-role="new-category-form"]');
  if (!form) return;
  e.preventDefault();
  const nameInput = form.querySelector('[data-role="new-name-input"]');
  const kindSelect = form.querySelector('[data-role="new-kind-select"]');
  handleCreateCategory(nameInput, kindSelect);
}

function focusNameInput(id) {
  const el = rootEl.querySelector(`[data-role="name-input"][data-id="${id}"]`);
  if (el) {
    el.focus();
    el.select();
  }
}

function focusKindSelect(id) {
  const el = rootEl.querySelector(`[data-role="kind-select"][data-id="${id}"]`);
  if (el) el.focus();
}

function focusNewCategoryName() {
  const el = rootEl.querySelector('[data-role="new-name-input"]');
  if (el) el.focus();
}

function focusBudgetInput(id) {
  const el = rootEl.querySelector(`[data-role="budget-input"][data-id="${id}"]`);
  if (el) {
    el.focus();
    el.select();
  }
}

// -------------------------------- Rendering --------------------------------

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (ch) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
  ));
}

function errorDetail(err, fallback) {
  if (typeof err.detail === "string") return err.detail;
  if (Array.isArray(err.detail)) return err.detail.map((item) => item.msg).join(" ");
  return fallback;
}

function paint() {
  rootEl.innerHTML = `
    <div class="categories-page">
      <header class="categories-intro">
        <p class="eyebrow">Budgets</p>
        <h1 class="categories-intro-title">Categories</h1>
        <p class="categories-intro-lede">A little context for your spending, with room to adjust.</p>
      </header>
      ${renderTrackerCard()}
      ${renderManageCard()}
    </div>
  `;
}

// --- Monthly expense budgets ---

function renderTrackerCard() {
  const monthName = MONTH_NAMES[state.month];
  const navHtml = `
    <div class="month-nav" role="group" aria-label="Month">
      <button type="button" class="month-nav-btn" data-action="prev-month" aria-label="Previous month">${ICON_CHEVRON_LEFT}</button>
      <span class="month-nav-label u-tabular">${monthName} ${state.year}</span>
      <button type="button" class="month-nav-btn" data-action="next-month" aria-label="Next month">${ICON_CHEVRON_RIGHT}</button>
    </div>
  `;

  let bodyHtml;
  if (state.loadingSummary) {
    bodyHtml = trackerSkeleton();
  } else if (state.summaryError) {
    bodyHtml = `
      <div class="empty-state">
        <p class="empty-state-title">Couldn't load this month</p>
        <p class="empty-state-body">${escapeHtml(state.summaryError)}</p>
        <button type="button" class="btn btn-ghost" data-action="retry-summary">Try again</button>
      </div>
    `;
  } else {
    bodyHtml = renderTrackerBody();
  }

  return `
    <section class="card tracker-card">
      <div class="tracker-header">
        <div>
          <h2 class="section-title">Expense budgets</h2>
          <p class="tracker-lede">Monthly budgets carry forward. Refunds reduce the category's spending.</p>
        </div>
        ${navHtml}
      </div>
      ${bodyHtml}
    </section>
  `;
}

function trackerSkeleton() {
  return `
    <div class="category-budget-grid" aria-label="Loading expense budgets">
      ${[0, 1, 2, 3]
        .map(
          () => `
        <div class="category-budget-card">
          <div class="category-bar-row-top">
            <span class="skeleton skeleton-text" style="width:40%"></span>
            <span class="skeleton skeleton-text" style="width:15%"></span>
          </div>
          <div class="skeleton skeleton-text" style="width:55%"></div>
          <div class="skeleton category-bar-track-skeleton"></div>
        </div>
      `
        )
        .join("")}
    </div>
  `;
}

// Zero-spend expense categories stay visible so their budgets can be set.
// Income and savings categories remain editable in management below.
function renderTrackerBody() {
  const summary = state.summary;
  if (!summary) return "";

  const expenseRows = summary.categories
    .filter((c) => c.kind === "expense")
    .sort((a, b) => b.spent_cents - a.spent_cents || a.name.localeCompare(b.name));
  const cards = expenseRows.length
    ? `<div class="category-budget-grid">${expenseRows.map(renderBudgetCard).join("")}</div>`
    : `
      <div class="empty-state tracker-empty">
        <p class="empty-state-title">Room for your own categories</p>
        <p class="empty-state-body">Add an expense category below to give its spending a monthly budget.</p>
      </div>
    `;
  const uncategorized = summary.uncategorized_cents !== 0
    ? `<div class="category-uncategorized">
        <div><strong>Uncategorized activity</strong><p>Still available to sort in transactions.</p></div>
        <span class="u-tabular">${escapeHtml(summary.uncategorized_display)}</span>
        <a class="link" href="#/transactions">Review</a>
      </div>`
    : "";
  return `${cards}${uncategorized}`;
}

function percentGeometry(value) {
  return Number.isFinite(value) ? Math.max(0, Math.min(100, value)) : 0;
}

function renderBudgetCard(category) {
  const id = category.id;
  const hasBudget = category.budget_cents !== null;
  const hasPercent = Number.isFinite(category.budget_used_percent);
  const percent = percentGeometry(category.budget_used_percent);
  const percentLabel = hasPercent ? `${Math.round(category.budget_used_percent)}%` : "—";
  const activityOpen = !!state.activity[id]?.open;
  const remaining = category.remaining_cents !== null
    ? `<div><dt>${escapeHtml(category.remaining_label)}</dt><dd class="u-tabular">${escapeHtml(category.remaining_magnitude_display)}</dd></div>`
    : "";
  const progress = hasPercent
    ? `<div class="category-budget-progress" aria-label="${escapeHtml(percentLabel)} of monthly budget used">
        <div class="category-budget-progress-fill" style="width:${percent}%"></div>
        <span class="category-budget-month-marker" style="left:${percentGeometry(category.month_elapsed_percent)}%" aria-hidden="true"></span>
      </div>
      <p class="category-budget-progress-caption">${percentLabel} used · marker shows month elapsed</p>`
    : "";
  return `
    <article class="category-budget-card" style="--bar-hue:var(--cat-hue-${id % DATA_HUE_COUNT})">
      <div class="category-budget-heading">
        <div class="category-budget-title">
          <span class="category-dot" style="--dot-hue:var(--bar-hue)" aria-hidden="true"></span>
          <h3>${escapeHtml(category.name)}</h3>
        </div>
        <div class="category-budget-ring" aria-label="${hasPercent ? `${percentLabel} of budget used` : "No budget percentage"}">
          <svg viewBox="0 0 40 40" aria-hidden="true">
            <circle class="category-budget-ring-track" cx="20" cy="20" r="16" />
            <circle class="category-budget-ring-fill" cx="20" cy="20" r="16" pathLength="100" stroke-dasharray="${percent} 100" />
          </svg>
          <span class="u-tabular">${percentLabel}</span>
        </div>
      </div>
      <dl class="category-budget-values">
        <div><dt>${category.spent_cents < 0 ? "Net spent" : "Spent"}</dt><dd class="category-budget-spent u-tabular">${escapeHtml(category.spent_display)}</dd></div>
        <div><dt>Monthly budget</dt><dd class="u-tabular">${hasBudget ? escapeHtml(category.budget_display) : "Not set"}</dd></div>
        ${remaining}
      </dl>
      <div class="category-budget-context">
        <p class="category-budget-pace">${escapeHtml(category.pace_label)}</p>
        ${category.projected_display != null ? `<p class="category-budget-projection">At this pace: <strong class="u-tabular">${escapeHtml(category.projected_display)}</strong> this month</p>` : ""}
        ${progress}
      </div>
      ${state.editingBudgetId === id ? renderBudgetForm(category) : `
        <button type="button" class="btn btn-quiet category-budget-edit" data-action="edit-budget" data-id="${id}" ${state.budgetSaving !== null ? "disabled" : ""}>${hasBudget ? "Edit budget" : "Set budget"}<span class="sr-only"> for ${escapeHtml(category.name)}</span></button>
      `}
      <button type="button" class="category-activity-toggle${activityOpen ? " is-open" : ""}" data-action="toggle-activity" data-id="${id}" aria-expanded="${activityOpen}" aria-controls="category-activity-${id}">
        <span>Transactions</span>${ICON_CHEVRON_DOWN}
      </button>
      ${activityOpen ? renderCategoryActivity(id) : ""}
    </article>
  `;
}

function renderBudgetForm(category) {
  const id = category.id;
  const saving = state.budgetSaving === id;
  const error = state.budgetErrors[id];
  return `
    <form class="category-budget-form" data-role="budget-form" data-id="${id}">
      <label class="field-label" for="category-budget-${id}">Monthly budget for ${escapeHtml(category.name)}</label>
      <input class="input" id="category-budget-${id}" data-role="budget-input" data-id="${id}" type="text" inputmode="decimal" value="${escapeHtml(state.budgetDrafts[id] ?? "")}" placeholder="e.g. 600.00" ${saving ? "disabled" : ""} aria-describedby="category-budget-hint-${id}${error ? ` category-budget-error-${id}` : ""}" ${error ? 'aria-invalid="true"' : ""}>
      <p class="field-hint" id="category-budget-hint-${id}">Applies each month. Leave blank to remove the budget.</p>
      ${error ? `<p class="field-error" id="category-budget-error-${id}" role="alert">${escapeHtml(error)}</p>` : ""}
      <div class="category-budget-form-actions">
        <button type="submit" class="btn btn-primary" ${saving ? "disabled" : ""}>${saving ? "Saving…" : "Save budget"}</button>
        <button type="button" class="btn btn-quiet" data-action="cancel-budget" data-id="${id}" ${saving ? "disabled" : ""}>Cancel</button>
      </div>
    </form>
  `;
}

function accountLabel(transaction) {
  const account = state.accounts.find((item) => item.id === transaction.account_id || item.display_name === transaction.account_display_name);
  const name = account?.name || transaction.account_display_name || "Account";
  const lastFour = account?.account_number;
  return lastFour && !name.includes(lastFour) ? `${name} · •••• ${lastFour}` : name;
}

function activityDate(value) {
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

function renderCategoryActivity(id) {
  const activity = state.activity[id];
  let content;
  if (activity.loading) {
    content = `<p class="category-activity-note" role="status">Loading transactions…</p>`;
  } else if (activity.error) {
    content = `<p class="field-error" role="alert">${escapeHtml(activity.error)}</p>
      <button type="button" class="btn btn-quiet" data-action="activity-page" data-id="${id}" data-offset="${activity.requestedOffset}">Try again</button>`;
  } else if (!activity.rows?.length) {
    content = `<p class="category-activity-note">No transactions in ${MONTH_NAMES[state.month]}.</p>`;
  } else {
    const rows = activity.rows.map((transaction) => `
      <li class="category-activity-row">
        <div class="category-activity-description">
          <p>${escapeHtml(transaction.merchant_name || transaction.description)}</p>
          <p class="category-activity-subtext">${escapeHtml(activityDate(transaction.posted_on))} · ${escapeHtml(accountLabel(transaction))}</p>
        </div>
        <span class="category-activity-amount u-tabular">${escapeHtml(transaction.amount_display)}<span>${escapeHtml(transaction.currency)}</span></span>
      </li>
    `).join("");
    const end = activity.offset + activity.rows.length;
    const pagination = activity.total > 20 ? `
      <div class="category-activity-pagination">
        <button type="button" class="btn btn-quiet" data-action="activity-page" data-id="${id}" data-offset="${Math.max(0, activity.offset - 20)}" ${activity.offset === 0 ? "disabled" : ""}>Previous</button>
        <span class="u-tabular">${activity.offset + 1}–${end} of ${activity.total}</span>
        <button type="button" class="btn btn-quiet" data-action="activity-page" data-id="${id}" data-offset="${end}" ${end >= activity.total ? "disabled" : ""}>Next</button>
      </div>` : "";
    content = `<p class="category-activity-note">Bank transactions · full amounts</p><ul>${rows}</ul>${pagination}`;
  }
  return `
    <div class="category-activity" id="category-activity-${id}">
      ${state.accountsError ? `<p class="category-activity-note">${escapeHtml(state.accountsError)} <button class="link" type="button" data-action="retry-accounts">Try again</button></p>` : ""}
      ${content}
    </div>
  `;
}

// --- Manage: "Your categories" ---

function renderManageCard() {
  if (state.loadingCategories) {
    return `<section class="card manage-card">${manageSkeleton()}</section>`;
  }

  if (state.categoriesError) {
    return `
      <section class="card manage-card">
        <div class="empty-state">
          <p class="empty-state-title">Couldn't load your categories</p>
          <p class="empty-state-body">${escapeHtml(state.categoriesError)}</p>
          <button type="button" class="btn btn-ghost" data-action="retry-categories">Try again</button>
        </div>
      </section>
    `;
  }

  const categories = state.categories || [];
  const isEmpty = categories.length === 0;

  const headerHtml = `
    <div class="manage-header">
      <h2 class="section-title">Your categories</h2>
    </div>
  `;

  const formHtml = state.showNewForm || isEmpty ? renderNewCategoryForm(isEmpty) : "";

  let listHtml;
  if (isEmpty) {
    listHtml = `
      <div class="empty-state manage-empty-state">
        <p class="empty-state-title">Categories are how BlitZave learns where things go</p>
        <p class="empty-state-body">Add a few to start sorting transactions — you can always rename or regroup them later.</p>
      </div>
    `;
  } else {
    listHtml = KIND_ORDER.map((kind) =>
      renderKindGroup(kind, categories.filter((c) => c.kind === kind))
    ).join("");
  }

  // A tappable "add" row at the foot of the list, Apple-list-row style —
  // a small accent-tinted plus affordance, not a header button — rather
  // than the inline form the empty state already shows.
  const addRowHtml = !isEmpty && !state.showNewForm ? renderCategoryAddRow() : "";

  return `
    <section class="card manage-card">
      ${headerHtml}
      ${formHtml}
      ${listHtml}
      ${addRowHtml}
    </section>
  `;
}

function renderCategoryAddRow() {
  return `
    <button type="button" class="category-add-row" data-action="new-category-toggle">
      <span class="category-add-icon-wrap">${ICON_PLUS}</span>
      <span>New category</span>
    </button>
  `;
}

function manageSkeleton() {
  return `
    <div class="manage-header">
      <span class="skeleton skeleton-text" style="width:140px;height:1.1em"></span>
    </div>
    <div class="category-list-skeleton">
      ${[0, 1, 2].map(() => `<div class="skeleton category-row-skeleton"></div>`).join("")}
    </div>
  `;
}

function renderKindGroup(kind, cats) {
  if (!cats.length) return "";
  return `
    <div class="category-group">
      <h3 class="category-group-title">${KIND_LABELS[kind]}</h3>
      <ul class="category-list">
        ${cats.map(renderCategoryRow).join("")}
      </ul>
    </div>
  `;
}

function renderCategoryRow(cat) {
  const id = cat.id;
  const isEditingName = state.editingNameId === id;
  const isEditingKind = state.editingKindId === id;
  const isConfirmingDelete = state.confirmDeleteId === id;
  const rowError = state.rowErrors[id];

  const nameHtml = isEditingName
    ? `<input type="text" class="input category-name-input" data-role="name-input" data-id="${id}"
         aria-label="Category name" value="${escapeHtml(state.pendingNameValue[id] ?? cat.name)}" />`
    : `<button type="button" class="category-name" data-action="start-rename" data-id="${id}">${escapeHtml(cat.name)}</button>`;

  const kindHtml = isEditingKind
    ? `<select class="select category-kind-select" data-role="kind-select" data-id="${id}" aria-label="Category kind">
        ${KIND_ORDER.map(
          (k) => `<option value="${k}" ${k === cat.kind ? "selected" : ""}>${KIND_LABELS[k]}</option>`
        ).join("")}
      </select>`
    : `<button type="button" class="badge category-kind-badge" data-action="start-kind-edit" data-id="${id}">${KIND_LABELS[cat.kind]}</button>`;

  const actionsHtml = isConfirmingDelete
    ? `
      <span class="category-confirm">
        <span class="category-confirm-label">Delete this category?</span>
        <button type="button" class="btn btn-danger" data-action="confirm-delete" data-id="${id}">Delete</button>
        <button type="button" class="btn btn-quiet" data-action="cancel-delete" data-id="${id}">Cancel</button>
      </span>
    `
    : `<button type="button" class="category-menu-btn" data-action="toggle-delete-confirm" data-id="${id}" aria-label="More actions for ${escapeHtml(cat.name)}">${ICON_ELLIPSIS}</button>`;

  // Same hue as the category's bar in the tracker; only expense categories
  // appear there, so income and savings keep the neutral default dot.
  const dotStyle =
    cat.kind === "expense" ? ` style="--dot-hue:var(--cat-hue-${id % DATA_HUE_COUNT})"` : "";

  // A name collision or an in-use delete is a "not yet," not a failure —
  // it gets a quiet note. Coral is only for a request that really errored.
  const errorHtml = rowError
    ? `<p class="${rowError.note ? "category-note" : "field-error"} category-row-error">${escapeHtml(rowError.text)}${
        rowError.link ? ` <a class="link" href="#/transactions">See transactions</a>` : ""
      }</p>`
    : "";

  return `
    <li class="category-row">
      <div class="category-row-top">
        <div class="category-row-main">
          <span class="category-dot"${dotStyle} aria-hidden="true"></span>
          ${nameHtml}
          ${kindHtml}
        </div>
        <div class="category-row-actions">${actionsHtml}</div>
      </div>
      ${errorHtml}
    </li>
  `;
}

function renderNewCategoryForm(isEmpty) {
  const f = state.newForm;
  return `
    <form class="new-category-form" data-role="new-category-form">
      <div class="field new-category-field-name">
        <label class="field-label" for="new-category-name">Name</label>
        <input id="new-category-name" type="text" class="input" data-role="new-name-input"
          value="${escapeHtml(f.name)}" placeholder="e.g. Groceries" />
      </div>
      <div class="field new-category-field-kind">
        <label class="field-label" for="new-category-kind">Kind</label>
        <select id="new-category-kind" class="select" data-role="new-kind-select">
          ${KIND_ORDER.map(
            (k) => `<option value="${k}" ${k === f.kind ? "selected" : ""}>${KIND_LABELS[k]}</option>`
          ).join("")}
        </select>
      </div>
      <div class="new-category-form-actions">
        <button type="submit" class="btn btn-primary" ${f.saving ? "disabled" : ""}>
          ${f.saving ? "Adding…" : "Add category"}
        </button>
        ${!isEmpty ? `<button type="button" class="btn btn-quiet" data-action="new-category-cancel">Cancel</button>` : ""}
      </div>
      ${f.error ? `<p class="${f.note ? "category-note" : "field-error"} new-category-form-error">${escapeHtml(f.error)}</p>` : ""}
    </form>
  `;
}
