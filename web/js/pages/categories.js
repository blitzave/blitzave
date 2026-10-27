// BlitZave — categories page.
// One list of every category: monthly budget figures for expense categories,
// and rename / type / delete tucked into each row's expanded panel.
// "Transactions" links out to the transactions page, filtered to the
// category and month — this page never lists transactions itself.
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

const ICON_PLUS = `
  <svg class="category-add-icon" viewBox="0 0 24 24" aria-hidden="true">
    <path d="M12 5v14M5 12h14" />
  </svg>
`;

const ICON_ARROW_RIGHT = `
  <svg class="budget-link-icon" viewBox="0 0 24 24" aria-hidden="true">
    <path d="M5 12h14M13 6l6 6-6 6" />
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
    editingBudgetId: null,
    budgetDrafts: {},
    budgetErrors: {},
    budgetSaving: null,
    expandedBudgets: {},
    showIdleBudgets: false,
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
  const [categoriesResult, summaryResult] = await Promise.allSettled([
    api.listCategories(),
    api.categorySummary(start, end),
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
  state.editingBudgetId = null;
  loadSummaryForMonth(token);
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
    case "toggle-budget-row": {
      if (state.budgetSaving === id || state.kindSaving === id) break;
      const closing = isRowOpen(id);
      state.expandedBudgets[id] = !closing;
      if (closing) {
        // Collapsing a row also backs out of anything open inside it.
        if (state.editingBudgetId === id) state.editingBudgetId = null;
        if (state.editingNameId === id) state.editingNameId = null;
        if (state.editingKindId === id) state.editingKindId = null;
        if (state.confirmDeleteId === id) state.confirmDeleteId = null;
        state.rowErrors[id] = null;
      }
      paint();
      break;
    }
    case "toggle-idle-budgets":
      state.showIdleBudgets = !state.showIdleBudgets;
      paint();
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
      ${renderCategoriesSection()}
    </div>
  `;
}

// --- The one categories list ---

function renderCategoriesSection() {
  const monthName = MONTH_NAMES[state.month];
  const navHtml = `
    <div class="month-nav" role="group" aria-label="Month">
      <button type="button" class="month-nav-btn" data-action="prev-month" aria-label="Previous month">${ICON_CHEVRON_LEFT}</button>
      <span class="month-nav-label u-tabular">${monthName} ${state.year}</span>
      <button type="button" class="month-nav-btn" data-action="next-month" aria-label="Next month">${ICON_CHEVRON_RIGHT}</button>
    </div>
  `;

  let bodyHtml;
  if (state.loadingCategories || state.loadingSummary) {
    bodyHtml = listSkeleton();
  } else if (state.categoriesError) {
    bodyHtml = errorCard("Couldn't load your categories", state.categoriesError, "retry-categories");
  } else if (state.summaryError) {
    bodyHtml = errorCard("Couldn't load this month", state.summaryError, "retry-summary");
  } else {
    bodyHtml = renderListBody();
  }

  return `
    <section class="categories-section">
      <div class="tracker-header">
        <div>
          <h2 class="section-title">Your categories</h2>
          <p class="tracker-lede">Budgets carry forward each month. Refunds reduce a category's spending.</p>
        </div>
        ${navHtml}
      </div>
      ${bodyHtml}
    </section>
  `;
}

function errorCard(title, detail, retryAction) {
  return `
    <div class="card">
      <div class="empty-state">
        <p class="empty-state-title">${title}</p>
        <p class="empty-state-body">${escapeHtml(detail)}</p>
        <button type="button" class="btn btn-ghost" data-action="${retryAction}">Try again</button>
      </div>
    </div>
  `;
}

function listSkeleton() {
  return `
    <div class="card budget-list" aria-label="Loading categories">
      ${[0, 1, 2, 3]
        .map(
          () => `
        <div class="budget-row-skeleton">
          <span class="skeleton skeleton-text" style="width:22%"></span>
          <span class="skeleton category-bar-track-skeleton"></span>
          <span class="skeleton skeleton-text" style="width:16%"></span>
        </div>
      `
        )
        .join("")}
    </div>
  `;
}

// Groups, top to bottom, so the categories that matter read at a glance:
//   1. expense with a budget, most of their budget used first;
//   2. expense with spending but no budget, biggest first;
//   3. expense with no budget and no spending, folded behind one line;
//   4. income, then savings — no budget figures, just the manage tools.
// Membership comes from the categories list; this month's figures are
// looked up from the summary by id.
function renderListBody() {
  const categories = state.categories || [];
  if (!categories.length) {
    return `
      <div class="card budget-list">
        <div class="empty-state manage-empty-state">
          <p class="empty-state-title">Categories are how BlitZave learns where things go</p>
          <p class="empty-state-body">Add a few to start sorting transactions — you can always rename or regroup them later.</p>
        </div>
        ${renderNewCategoryForm(true)}
      </div>
    `;
  }

  const figures = new Map((state.summary?.categories || []).map((c) => [c.id, c]));
  const byName = (a, b) => a.name.localeCompare(b.name);
  const usedOf = (c) => (Number.isFinite(c.budget_used_percent) ? c.budget_used_percent : -Infinity);

  const expense = categories
    .filter((c) => c.kind === "expense")
    .map((c) => ({ ...c, ...(figures.get(c.id) || {}), name: c.name, kind: c.kind }));
  const hasBudget = (c) => c.budget_cents != null;
  const spent = (c) => c.spent_cents || 0;
  const budgeted = expense
    .filter(hasBudget)
    .sort((a, b) => usedOf(b) - usedOf(a) || spent(b) - spent(a) || byName(a, b));
  const unbudgeted = expense
    .filter((c) => !hasBudget(c) && spent(c) !== 0)
    .sort((a, b) => spent(b) - spent(a) || byName(a, b));
  const idle = expense.filter((c) => !hasBudget(c) && spent(c) === 0).sort(byName);
  const income = categories.filter((c) => c.kind === "income").sort(byName);
  const savings = categories.filter((c) => c.kind === "savings").sort(byName);

  const group = (label, rows, renderRow) => rows.length
    ? `<div class="budget-group">
        <p class="budget-group-label">${label}<span class="u-tabular">${rows.length}</span></p>
        <ul class="budget-rows">${rows.map(renderRow).join("")}</ul>
      </div>`
    : "";
  const idleBlock = idle.length
    ? `<div class="budget-group budget-group-idle">
        <button type="button" class="budget-idle-toggle${state.showIdleBudgets ? " is-open" : ""}" data-action="toggle-idle-budgets" aria-expanded="${state.showIdleBudgets}">
          <span>No spending yet this month<span class="budget-idle-names">${escapeHtml(idle.map((c) => c.name).join(", "))}</span></span>
          <span class="budget-idle-count u-tabular">${idle.length}</span>${ICON_CHEVRON_DOWN}
        </button>
        ${state.showIdleBudgets ? `<ul class="budget-rows">${idle.map(renderBudgetRow).join("")}</ul>` : ""}
      </div>`
    : "";

  const uncategorized = state.summary && state.summary.uncategorized_cents !== 0
    ? `<div class="category-uncategorized">
        <div><strong>Uncategorized activity</strong><p>Still available to sort in transactions.</p></div>
        <span class="u-tabular">${escapeHtml(state.summary.uncategorized_display)}</span>
        <a class="link" href="#/transactions">Review</a>
      </div>`
    : "";

  return `
    <div class="card budget-list">
      ${group("With a budget", budgeted, renderBudgetRow)}
      ${group("No budget yet", unbudgeted, renderBudgetRow)}
      ${idleBlock}
      ${group("Income", income, renderPlainRow)}
      ${group("Savings", savings, renderPlainRow)}
      <div class="budget-group budget-group-add">
        ${state.showNewForm ? renderNewCategoryForm(false) : renderCategoryAddRow()}
      </div>
    </div>
    ${uncategorized}
  `;
}

function percentGeometry(value) {
  return Number.isFinite(value) ? Math.max(0, Math.min(100, value)) : 0;
}

function isRowOpen(id) {
  return (
    !!state.expandedBudgets[id] ||
    state.editingBudgetId === id ||
    state.editingNameId === id ||
    state.editingKindId === id ||
    state.confirmDeleteId === id ||
    !!state.rowErrors[id]
  );
}

function rowHead(category, inner) {
  const id = category.id;
  const open = isRowOpen(id);
  const hue = category.kind === "expense" ? `var(--cat-hue-${id % DATA_HUE_COUNT})` : "var(--line-strong)";
  return `
    <button type="button" class="budget-row-head" data-action="toggle-budget-row" data-id="${id}" aria-expanded="${open}" aria-controls="budget-row-panel-${id}">
      <span class="budget-row-name">
        <span class="category-dot" style="--dot-hue:${hue}" aria-hidden="true"></span>
        <span>${escapeHtml(category.name)}</span>
      </span>
      ${inner}
      ${ICON_CHEVRON_DOWN}
    </button>
  `;
}

function renderBudgetRow(category) {
  const id = category.id;
  const hasBudget = category.budget_cents != null;
  const hasPercent = Number.isFinite(category.budget_used_percent);
  const percent = percentGeometry(category.budget_used_percent);
  const percentLabel = hasPercent ? `${Math.round(category.budget_used_percent)}%` : "";
  const open = isRowOpen(id);
  const meter = hasPercent
    ? `<span class="budget-row-meter" aria-hidden="true">
        <span class="budget-row-meter-fill" style="width:${percent}%"></span>
        ${Number.isFinite(category.month_elapsed_percent) ? `<span class="budget-row-marker" style="left:${percentGeometry(category.month_elapsed_percent)}%"></span>` : ""}
      </span>`
    : `<span class="budget-row-meter is-empty" aria-hidden="true"></span>`;
  const spentHtml = category.spent_display != null
    ? `<span class="budget-row-spent u-tabular">${escapeHtml(category.spent_display)}</span>`
    : "";
  const figure = hasBudget
    ? `${spentHtml}<span class="budget-row-of u-tabular">of ${escapeHtml(category.budget_display)}</span>`
    : `${spentHtml}<span class="budget-row-of">no budget</span>`;
  return `
    <li class="budget-row${open ? " is-open" : ""}" style="--bar-hue:var(--cat-hue-${id % DATA_HUE_COUNT})">
      ${rowHead(category, `${meter}<span class="budget-row-percent u-tabular">${percentLabel}</span><span class="budget-row-figure">${figure}</span>`)}
      ${open ? renderRowPanel(category) : ""}
    </li>
  `;
}

// Income and savings rows: no budget math, just the name and its tools.
function renderPlainRow(category) {
  const open = isRowOpen(category.id);
  return `
    <li class="budget-row budget-row-plain${open ? " is-open" : ""}">
      ${rowHead(category, `<span class="budget-row-kind">${KIND_LABELS[category.kind]}</span>`)}
      ${open ? renderRowPanel(category) : ""}
    </li>
  `;
}

function transactionsHref(id) {
  const month = `${state.year}-${String(state.month + 1).padStart(2, "0")}`;
  return `#/transactions?category=${encodeURIComponent(id)}&month=${month}`;
}

function renderRowPanel(category) {
  const id = category.id;
  const isExpense = category.kind === "expense";
  const hasBudget = category.budget_cents != null;

  let figuresHtml = "";
  if (isExpense && category.spent_display != null) {
    const remaining = category.remaining_cents != null
      ? `<div><dt>${escapeHtml(category.remaining_label)}</dt><dd class="u-tabular">${escapeHtml(category.remaining_magnitude_display)}</dd></div>`
      : "";
    figuresHtml = `
      <div class="budget-row-panel-grid">
        <dl class="category-budget-values">
          <div><dt>${category.spent_cents < 0 ? "Net spent" : "Spent"}</dt><dd class="u-tabular">${escapeHtml(category.spent_display)}</dd></div>
          <div><dt>Monthly budget</dt><dd class="u-tabular">${hasBudget ? escapeHtml(category.budget_display) : "Not set"}</dd></div>
          ${remaining}
        </dl>
        <div class="category-budget-context">
          <p class="category-budget-pace">${escapeHtml(category.pace_label)}</p>
          ${category.projected_display != null ? `<p class="category-budget-projection">At this pace: <strong class="u-tabular">${escapeHtml(category.projected_display)}</strong> this month</p>` : ""}
          ${Number.isFinite(category.month_elapsed_percent) && Number.isFinite(category.budget_used_percent) ? `<p class="category-budget-progress-caption">The marker on the bar shows how much of the month has passed.</p>` : ""}
        </div>
      </div>
    `;
  }

  const budgetHtml = isExpense && state.editingBudgetId === id ? renderBudgetForm(category) : "";
  const budgetBtn = isExpense && state.editingBudgetId !== id
    ? `<button type="button" class="btn btn-ghost" data-action="edit-budget" data-id="${id}" ${state.budgetSaving !== null ? "disabled" : ""}>${hasBudget ? "Edit budget" : "Set budget"}<span class="sr-only"> for ${escapeHtml(category.name)}</span></button>`
    : "";

  return `
    <div class="budget-row-panel" id="budget-row-panel-${id}">
      ${figuresHtml}
      ${budgetHtml}
      <div class="budget-row-actions">
        ${budgetBtn}
        <a class="btn btn-ghost budget-row-link" href="${transactionsHref(id)}">${MONTH_NAMES[state.month]} transactions${ICON_ARROW_RIGHT}</a>
        <span class="budget-row-actions-spacer"></span>
        ${renderManageTools(category)}
      </div>
      ${renderRowError(category)}
    </div>
  `;
}

// Rename / change type / delete. One tool is active at a time; the others
// step aside while it is.
function renderManageTools(category) {
  const id = category.id;
  if (state.editingNameId === id) {
    return `<input type="text" class="input category-name-input" data-role="name-input" data-id="${id}"
      aria-label="Category name" value="${escapeHtml(state.pendingNameValue[id] ?? category.name)}" />`;
  }
  if (state.editingKindId === id) {
    return `<select class="select category-kind-select" data-role="kind-select" data-id="${id}" aria-label="Category type">
      ${KIND_ORDER.map((k) => `<option value="${k}" ${k === category.kind ? "selected" : ""}>${KIND_LABELS[k]}</option>`).join("")}
    </select>`;
  }
  if (state.confirmDeleteId === id) {
    return `
      <span class="category-confirm">
        <span class="category-confirm-label">Delete this category?</span>
        <button type="button" class="btn btn-danger" data-action="confirm-delete" data-id="${id}">Delete</button>
        <button type="button" class="btn btn-quiet" data-action="cancel-delete" data-id="${id}">Cancel</button>
      </span>
    `;
  }
  return `
    <span class="budget-row-tools">
      <button type="button" class="btn btn-quiet" data-action="start-rename" data-id="${id}">Rename</button>
      <button type="button" class="btn btn-quiet" data-action="start-kind-edit" data-id="${id}">Type: ${KIND_LABELS[category.kind]}</button>
      <button type="button" class="btn btn-quiet" data-action="toggle-delete-confirm" data-id="${id}">Delete</button>
    </span>
  `;
}

// A name collision or an in-use delete is a "not yet," not a failure —
// it gets a quiet note. Red is only for a request that really errored.
function renderRowError(category) {
  const rowError = state.rowErrors[category.id];
  if (!rowError) return "";
  return `<p class="${rowError.note ? "category-note" : "field-error"} category-row-error">${escapeHtml(rowError.text)}${
    rowError.link ? ` <a class="link" href="#/transactions?category=${encodeURIComponent(category.id)}">See its transactions</a>` : ""
  }</p>`;
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

function renderCategoryAddRow() {
  return `
    <button type="button" class="category-add-row" data-action="new-category-toggle">
      <span class="category-add-icon-wrap">${ICON_PLUS}</span>
      <span>New category</span>
    </button>
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
