// BlitZave — categories page.
// Owned by the categories agent. Two sections, tracker first ("calm on
// top"): "Where it went" (the month's spend breakdown by category) above
// "Your categories" (create/rename/recategorize/delete).
//
// Money rule: every figure on screen is a *_display string straight from
// the API. Cents are only ever used here for bar-width geometry.

import * as api from "../api.js";

const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

const KIND_ORDER = ["expense", "income", "savings"];
const KIND_LABELS = { expense: "Expense", income: "Income", savings: "Savings" };

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
    pendingNameValue: {},
    rowErrors: {},
    showNewForm: false,
    newForm: { name: "", kind: "expense", error: null, saving: false },
  };
}

function stale(myToken) {
  return myToken !== token;
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

  if (summaryResult.status === "fulfilled") {
    state.summary = summaryResult.value;
    state.summaryError = null;
  } else {
    state.summaryError = summaryResult.reason?.detail || "Couldn't load this month.";
  }

  state.loadingCategories = false;
  state.loadingSummary = false;
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
  const { start, end } = monthRange(state.year, state.month);
  const [categories, summary] = await Promise.all([
    api.listCategories(),
    api.categorySummary(start, end),
  ]);
  state.categories = categories;
  state.summary = summary;
  state.categoriesError = null;
  state.summaryError = null;
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
  loadSummaryForMonth(token);
}

// ------------------------------- Mutations -------------------------------

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
    state.rowErrors[id] = {
      text:
        err.status === 409
          ? `Already have a category named "${value}."`
          : err.detail || "Couldn't save that. Try again.",
    };
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
        ? { text: "Still in use by some transactions — recategorize them first.", link: true }
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
    state.newForm = { name: nameInput.value, kind, error: "Give it a name first.", saving: false };
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

// -------------------------------- Rendering --------------------------------

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (ch) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
  ));
}

function paint() {
  rootEl.innerHTML = `
    <div class="categories-page">
      ${renderTrackerCard()}
      ${renderManageCard()}
    </div>
  `;
}

// --- Tracker: "Where it went" ---

function renderTrackerCard() {
  const monthName = MONTH_NAMES[state.month];
  const navHtml = `
    <div class="month-nav">
      <button type="button" class="month-nav-btn" data-action="prev-month" aria-label="Previous month">‹</button>
      <span class="month-nav-label u-tabular">${monthName} ${state.year}</span>
      <button type="button" class="month-nav-btn" data-action="next-month" aria-label="Next month">›</button>
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
        <h2 class="card-title">Where it went in ${monthName}</h2>
        ${navHtml}
      </div>
      ${bodyHtml}
    </section>
  `;
}

function trackerSkeleton() {
  return `
    <div class="category-bars">
      ${[0, 1, 2, 3]
        .map(
          () => `
        <div class="category-bar-row">
          <div class="category-bar-row-top">
            <span class="skeleton skeleton-text" style="width:40%"></span>
            <span class="skeleton skeleton-text" style="width:15%"></span>
          </div>
          <div class="skeleton category-bar-track-skeleton"></div>
        </div>
      `
        )
        .join("")}
    </div>
  `;
}

// The tracker only ever shows spending (expense-kind categories) plus the
// uncategorized bucket. Income and savings categories are excluded on
// purpose: a single paycheck would dwarf every expense bar on the same
// scale, turning "where it went" into a chart about income. They're still
// fully visible and editable in "Your categories" below — this is a
// display-only narrowing for the tracker, not a data restriction.
function renderTrackerBody() {
  const summary = state.summary;
  if (!summary) return "";

  const expenseRows = summary.categories.filter((c) => c.kind === "expense");
  const active = expenseRows.filter((c) => c.total_cents !== 0);
  const quiet = expenseRows.filter((c) => c.total_cents === 0);

  const items = active.map((c) => ({
    name: c.name,
    cents: Math.abs(c.total_cents),
    display: c.total_display,
    isUncategorized: false,
  }));
  items.push({
    name: "Uncategorized",
    cents: Math.abs(summary.uncategorized_cents),
    display: summary.uncategorized_display,
    isUncategorized: true,
  });
  items.sort((a, b) => b.cents - a.cents);

  const maxCents = Math.max(1, ...items.map((i) => i.cents));
  const barsHtml = items.map((item) => renderBarRow(item, maxCents)).join("");

  const quietHtml = quiet.length
    ? `
    <details class="quiet-categories">
      <summary class="quiet-categories-summary">${quiet.length} quiet this month</summary>
      <ul class="quiet-categories-list">
        ${quiet
          .map(
            (c) => `
          <li>
            <span>${escapeHtml(c.name)}</span>
            <span class="u-tabular">${escapeHtml(c.total_display)}</span>
          </li>
        `
          )
          .join("")}
      </ul>
    </details>
  `
    : "";

  return `<div class="category-bars">${barsHtml}</div>${quietHtml}`;
}

function renderBarRow(item, maxCents) {
  const pct = Math.min(100, Math.round((item.cents / maxCents) * 100));
  return `
    <div class="category-bar-row">
      <div class="category-bar-row-top">
        <span class="category-bar-name${item.isUncategorized ? " is-muted" : ""}">${escapeHtml(item.name)}</span>
        <span class="category-bar-amount u-tabular">${escapeHtml(item.display)}</span>
      </div>
      <div class="category-bar-track">
        <div class="category-bar-fill" style="width:${pct}%"></div>
      </div>
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
      <h2 class="card-title">Your categories</h2>
      ${
        !isEmpty && !state.showNewForm
          ? `<button type="button" class="btn btn-ghost" data-action="new-category-toggle">New category</button>`
          : ""
      }
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

  return `
    <section class="card manage-card">
      ${headerHtml}
      ${formHtml}
      ${listHtml}
    </section>
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
    : `<button type="button" class="btn-quiet category-menu-btn" data-action="toggle-delete-confirm" data-id="${id}" aria-label="More actions">⋯</button>`;

  const errorHtml = rowError
    ? `<p class="field-error category-row-error">${escapeHtml(rowError.text)}${
        rowError.link ? ` <a class="link" href="#/transactions">See transactions</a>` : ""
      }</p>`
    : "";

  return `
    <li class="category-row">
      <div class="category-row-top">
        <div class="category-row-main">
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
      ${f.error ? `<p class="field-error new-category-form-error">${escapeHtml(f.error)}</p>` : ""}
    </form>
  `;
}
