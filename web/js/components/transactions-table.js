// BlitZave — shared transactions table component.
//
// Self-fetching: owns its own filters, sorting, pagination, and per-row
// metadata editing. transactions.js (the full #/transactions page) and
// account-detail.js (the accounts agent's per-account view) both mount this
// the same way:
//
//   renderTransactionsTable(root, { accountId: null })  // every transaction
//   renderTransactionsTable(root, { accountId: "ACT-1" }) // scoped, no account filter
//
// Money rule: every figure a person reads comes straight from amount_display.
// amount_cents is only ever used to decide credit/debit color — never shown.

import * as api from "../api.js";

const PAGE_SIZE = 50;

/**
 * @param {HTMLElement} root
 * @param {{accountId?: string|null}} [options]
 */
export function renderTransactionsTable(root, { accountId = null } = {}) {
  const state = {
    q: "",
    start: "",
    end: "",
    categoryId: "",
    tagId: "",
    accountId: accountId || "",
    sort: "date",
    order: "desc",
    limit: PAGE_SIZE,
    offset: 0,
  };

  let categories = [];
  let tags = [];
  let accounts = [];
  let rows = [];
  let total = 0;

  let expandedId = null;
  let editorState = null; // { txId, catRows: [{categoryId, percent}], tagIds: Set, catError, tagError }

  let loadSeq = 0;
  let searchDebounce = null;

  const els = {};

  mountShell();
  init();

  // --------------------------------- shell ---------------------------------

  function mountShell() {
    root.innerHTML = `
      <div class="txns-filterbar"></div>
      <div class="card txns-table-wrap"></div>
      <div class="txns-pagination"></div>
    `;
    els.filterBar = root.querySelector(".txns-filterbar");
    els.tableWrap = root.querySelector(".txns-table-wrap");
    els.pagination = root.querySelector(".txns-pagination");

    els.tableWrap.addEventListener("click", handleTableWrapClick);
    els.tableWrap.addEventListener("change", handleTableWrapChange);
    els.tableWrap.addEventListener("input", handleTableWrapInput);
    els.tableWrap.addEventListener("keydown", handleTableWrapKeydown);
  }

  async function init() {
    renderTableSkeleton();
    try {
      const [cats, tagList, accts] = await Promise.all([
        api.listCategories(),
        api.listTags(),
        accountId ? Promise.resolve([]) : api.listAccounts(),
      ]);
      categories = cats || [];
      tags = tagList || [];
      accounts = accts || [];
    } catch (err) {
      renderFullError(err);
      return;
    }
    renderFilterBar();
    await loadPage();
  }

  function renderFullError(err) {
    root.innerHTML = `
      <div class="empty-state">
        <p class="empty-state-title">Couldn't load transactions</p>
        <p class="empty-state-body">${esc(err.detail || "Something went wrong.")}</p>
        <button type="button" class="btn btn-ghost txn-full-retry">Try again</button>
      </div>
    `;
    root.querySelector(".txn-full-retry").addEventListener("click", () => {
      mountShell();
      init();
    });
  }

  // ------------------------------- filter bar -------------------------------

  function filtersActive() {
    return Boolean(
      state.q ||
        state.start ||
        state.end ||
        state.categoryId ||
        state.tagId ||
        (!accountId && state.accountId)
    );
  }

  function byName(a, b) {
    return a.name.localeCompare(b.name);
  }

  function byDisplayName(a, b) {
    return a.display_name.localeCompare(b.display_name);
  }

  function optionHtml(id, label, selected) {
    // id may be a numeric DB id (from /api/categories, /api/tags) while
    // `selected` may be a string (an HTML <select>.value, or a key from
    // Object.entries(tx.categories)) — compare as strings so e.g. 1 === "1".
    return `<option value="${esc(id)}" ${String(id) === String(selected) ? "selected" : ""}>${esc(label)}</option>`;
  }

  function renderFilterBar() {
    const categoryOptions = categories.slice().sort(byName).map((c) => optionHtml(c.id, c.name, state.categoryId)).join("");
    const tagOptions = tags.slice().sort(byName).map((t) => optionHtml(t.id, t.name, state.tagId)).join("");
    const accountSelectHtml = accountId
      ? ""
      : `
        <select class="select txn-account-filter" aria-label="Filter by account">
          <option value="">All accounts</option>
          ${accounts.slice().sort(byDisplayName).map((a) => optionHtml(a.id, a.display_name, state.accountId)).join("")}
        </select>
      `;

    els.filterBar.innerHTML = `
      <div class="txn-filter-search">
        <input type="text" class="input txn-search" placeholder="Search description or merchant…"
          aria-label="Search transactions" value="${esc(state.q)}" />
      </div>
      <div class="txn-filter-dates">
        <input type="date" class="input txn-start-date" aria-label="From date" value="${esc(state.start)}" />
        <span class="txn-date-sep">–</span>
        <input type="date" class="input txn-end-date" aria-label="To date" value="${esc(state.end)}" />
      </div>
      <select class="select txn-category-filter" aria-label="Filter by category">
        <option value="">All categories</option>
        ${categoryOptions}
      </select>
      <select class="select txn-tag-filter" aria-label="Filter by tag">
        <option value="">All tags</option>
        ${tagOptions}
      </select>
      ${accountSelectHtml}
      <button type="button" class="link txn-clear-filters${filtersActive() ? "" : " is-hidden"}">Clear filters</button>
    `;
    wireFilterBar();
  }

  function refreshClearLink() {
    const link = els.filterBar.querySelector(".txn-clear-filters");
    if (link) link.classList.toggle("is-hidden", !filtersActive());
  }

  function wireFilterBar() {
    const search = els.filterBar.querySelector(".txn-search");
    search.addEventListener("input", () => {
      clearTimeout(searchDebounce);
      searchDebounce = setTimeout(() => {
        state.q = search.value.trim();
        state.offset = 0;
        refreshClearLink();
        loadPage();
      }, 300);
    });

    const start = els.filterBar.querySelector(".txn-start-date");
    start.addEventListener("change", () => {
      state.start = start.value;
      state.offset = 0;
      refreshClearLink();
      loadPage();
    });

    const end = els.filterBar.querySelector(".txn-end-date");
    end.addEventListener("change", () => {
      state.end = end.value;
      state.offset = 0;
      refreshClearLink();
      loadPage();
    });

    const catFilter = els.filterBar.querySelector(".txn-category-filter");
    catFilter.addEventListener("change", () => {
      state.categoryId = catFilter.value;
      state.offset = 0;
      refreshClearLink();
      loadPage();
    });

    const tagFilter = els.filterBar.querySelector(".txn-tag-filter");
    tagFilter.addEventListener("change", () => {
      state.tagId = tagFilter.value;
      state.offset = 0;
      refreshClearLink();
      loadPage();
    });

    const acctFilter = els.filterBar.querySelector(".txn-account-filter");
    if (acctFilter) {
      acctFilter.addEventListener("change", () => {
        state.accountId = acctFilter.value;
        state.offset = 0;
        refreshClearLink();
        loadPage();
      });
    }

    els.filterBar.querySelector(".txn-clear-filters").addEventListener("click", resetFilters);
  }

  function resetFilters() {
    state.q = "";
    state.start = "";
    state.end = "";
    state.categoryId = "";
    state.tagId = "";
    if (!accountId) state.accountId = "";
    state.offset = 0;
    renderFilterBar();
    loadPage();
  }

  // --------------------------------- fetch ---------------------------------

  function buildParams() {
    return {
      account_id: accountId || state.accountId,
      start: state.start,
      end: state.end,
      q: state.q,
      category_id: state.categoryId,
      tag_id: state.tagId,
      sort: state.sort,
      order: state.order,
      limit: state.limit,
      offset: state.offset,
    };
  }

  async function loadPage() {
    const seq = ++loadSeq;
    expandedId = null;
    editorState = null;
    renderTableSkeleton();
    els.pagination.innerHTML = "";
    try {
      const res = await api.listTransactions(buildParams());
      if (seq !== loadSeq) return;
      rows = res.transactions || [];
      total = res.total || 0;
      renderTable();
      renderPagination();
    } catch (err) {
      if (seq !== loadSeq) return;
      renderTableError(err);
    }
  }

  // --------------------------------- table ---------------------------------

  function renderTableSkeleton() {
    els.tableWrap.innerHTML = `
      <div class="txns-skeleton">
        ${Array.from({ length: 6 }).map(() => '<div class="skeleton txns-skeleton-row"></div>').join("")}
      </div>
    `;
  }

  function renderTableError(err) {
    els.tableWrap.innerHTML = `
      <div class="empty-state">
        <p class="empty-state-title">Couldn't load transactions</p>
        <p class="empty-state-body">${esc(err.detail || "Something went wrong.")}</p>
        <button type="button" class="btn btn-ghost txn-retry">Try again</button>
      </div>
    `;
  }

  function emptyStateHtml() {
    const anyFilter = filtersActive();
    return `
      <div class="empty-state">
        <p class="empty-state-title">${anyFilter ? "No transactions match" : "Nothing here yet"}</p>
        <p class="empty-state-body">${
          anyFilter
            ? "Try widening your filters."
            : "Sync your accounts and transactions will land here."
        }</p>
      </div>
    `;
  }

  function sortedClass(field) {
    if (state.sort !== field) return "";
    return state.order === "desc" ? "is-sorted is-sorted-desc" : "is-sorted";
  }

  function renderTable() {
    if (rows.length === 0) {
      els.tableWrap.innerHTML = emptyStateHtml();
      return;
    }
    els.tableWrap.innerHTML = `
      <table class="table txns-table">
        <thead>
          <tr>
            <th class="is-sortable ${sortedClass("date")}" data-sort="date">Date</th>
            <th class="is-sortable ${sortedClass("description")}" data-sort="description">Description</th>
            <th>Category</th>
            <th>Tags</th>
            <th class="is-sortable is-numeric ${sortedClass("amount")}" data-sort="amount">Amount</th>
          </tr>
        </thead>
        <tbody>${rows.map(rowHtml).join("")}</tbody>
      </table>
    `;
  }

  function rowHtml(tx) {
    const amountClass = tx.amount_cents > 0 ? "txn-amount-credit" : "txn-amount-debit";
    const merchantHtml =
      tx.merchant_name && tx.merchant_name !== tx.description
        ? `<div class="txn-merchant">${esc(tx.merchant_name)}</div>`
        : "";
    return `
      <tr class="txn-row" data-id="${esc(tx.id)}" tabindex="0" role="button" aria-expanded="false">
        <td class="txn-date-cell u-tabular">${esc(formatDate(tx.posted_on))}</td>
        <td class="txn-desc-cell">
          <div class="txn-desc">${esc(tx.description || "")}</div>
          ${merchantHtml}
        </td>
        <td class="txn-cat-cell">${categoryChipsMarkup(tx)}</td>
        <td class="txn-tag-cell">${tagChipsMarkup(tx)}</td>
        <td class="is-numeric u-tabular ${amountClass}">${esc(tx.amount_display)}</td>
      </tr>
    `;
  }

  function categoryChipsMarkup(tx) {
    const entries = Object.entries(tx.categories || {});
    if (entries.length === 0) {
      return '<span class="txn-uncategorized">Uncategorized</span>';
    }
    return entries
      .map(([catId, fraction]) => {
        const cat = categories.find((c) => String(c.id) === String(catId));
        const name = cat ? cat.name : "Unknown category";
        const pct = Math.round(fraction * 100);
        const label = entries.length > 1 ? `${esc(name)} ${pct}%` : esc(name);
        return `<span class="chip">${label}</span>`;
      })
      .join("");
  }

  function tagChipsMarkup(tx) {
    if (!tx.tags || tx.tags.length === 0) return "";
    return tx.tags.map((t) => `<span class="chip">${esc(t.name)}</span>`).join("");
  }

  // ------------------------------- pagination -------------------------------

  function renderPagination() {
    if (total === 0) {
      els.pagination.innerHTML = "";
      return;
    }
    const from = state.offset + 1;
    const to = Math.min(state.offset + state.limit, total);
    els.pagination.innerHTML = `
      <p class="txns-page-count u-tabular">Showing ${from}–${to} of ${total}</p>
      <div class="txns-page-btns">
        <button type="button" class="btn btn-ghost txn-prev" ${state.offset === 0 ? "disabled" : ""}>Previous</button>
        <button type="button" class="btn btn-ghost txn-next" ${to >= total ? "disabled" : ""}>Next</button>
      </div>
    `;
    els.pagination.querySelector(".txn-prev").addEventListener("click", () => {
      state.offset = Math.max(0, state.offset - state.limit);
      loadPage();
    });
    els.pagination.querySelector(".txn-next").addEventListener("click", () => {
      state.offset = state.offset + state.limit;
      loadPage();
    });
  }

  // --------------------------- row expand / collapse ---------------------------

  function findRowTr(id) {
    return Array.from(els.tableWrap.querySelectorAll("tr.txn-row")).find((tr) => tr.dataset.id === id) || null;
  }

  function findEditorTr(id) {
    return Array.from(els.tableWrap.querySelectorAll("tr.txn-editor-row")).find((tr) => tr.dataset.id === id) || null;
  }

  function toggleRow(tr) {
    const id = tr.dataset.id;
    if (expandedId === id) {
      collapseRow();
      return;
    }
    collapseRow();
    expandRow(tr, id);
  }

  function expandRow(tr, id) {
    const tx = rows.find((r) => r.id === id);
    if (!tx) return;

    expandedId = id;
    editorState = buildInitialEditorState(tx);

    tr.classList.add("is-expanded");
    tr.setAttribute("aria-expanded", "true");

    const editorTr = document.createElement("tr");
    editorTr.className = "txn-editor-row";
    editorTr.dataset.id = id;
    editorTr.innerHTML = `<td colspan="5"><div class="txn-editor-wrap"><div class="txn-editor">${editorPanelHtml(tx)}</div></div></td>`;
    tr.after(editorTr);

    const wrap = editorTr.querySelector(".txn-editor-wrap");
    requestAnimationFrame(() => {
      wrap.style.maxHeight = `${wrap.scrollHeight}px`;
    });
  }

  function collapseRow() {
    if (!expandedId) return;
    const prevId = expandedId;
    expandedId = null;
    editorState = null;

    const rowTr = findRowTr(prevId);
    if (rowTr) {
      rowTr.classList.remove("is-expanded");
      rowTr.setAttribute("aria-expanded", "false");
    }

    const editorTr = findEditorTr(prevId);
    if (!editorTr) return;
    const wrap = editorTr.querySelector(".txn-editor-wrap");
    const remove = () => editorTr.remove();
    wrap.style.maxHeight = "0px";
    wrap.addEventListener("transitionend", remove, { once: true });
    setTimeout(remove, 300); // fallback in case transitionend never fires
  }

  function resizeEditor() {
    const editorTr = expandedId ? findEditorTr(expandedId) : null;
    const wrap = editorTr ? editorTr.querySelector(".txn-editor-wrap") : null;
    if (wrap) wrap.style.maxHeight = `${wrap.scrollHeight}px`;
  }

  function currentEditTx() {
    if (!editorState) return null;
    return rows.find((r) => r.id === editorState.txId) || null;
  }

  // ----------------------------- editor: shared -----------------------------

  function buildInitialEditorState(tx) {
    const entries = Object.entries(tx.categories || {});
    const catRows = entries.map(([categoryId, fraction]) => ({
      categoryId,
      percent: Math.round(fraction * 100),
    }));
    return {
      txId: tx.id,
      catRows,
      // Stored as strings: dataset.tagId (used to toggle) is always a string,
      // while t.id from the API is numeric — normalize once here so
      // .has()/.add()/.delete() compare like-for-like everywhere below.
      tagIds: new Set((tx.tags || []).map((t) => String(t.id))),
      catError: null,
      tagError: null,
    };
  }

  function editorPanelHtml(tx) {
    return `
      <div class="txn-editor-section txn-cat-section">${categoriesSectionHtml(tx)}</div>
      <div class="txn-editor-divider"></div>
      <div class="txn-editor-section txn-tag-section">${tagsSectionHtml(tx)}</div>
      <p class="txn-editor-reminder">Amount, date, and description come from your bank — they can't be edited here.</p>
    `;
  }

  // ------------------------------ editor: categories ------------------------------

  function sumPercents() {
    return editorState.catRows.reduce((acc, r) => acc + (Number(r.percent) || 0), 0);
  }

  function catRowHtml(row, index) {
    const single = editorState.catRows.length === 1;
    const usedElsewhere = new Set(
      editorState.catRows.filter((_, i) => i !== index).map((r) => String(r.categoryId))
    );
    const options = [`<option value="">Choose a category…</option>`]
      .concat(
        categories
          .filter((c) => String(c.id) === String(row.categoryId) || !usedElsewhere.has(String(c.id)))
          .slice()
          .sort(byName)
          .map((c) => optionHtml(c.id, c.name, row.categoryId))
      )
      .join("");
    return `
      <div class="txn-split-row" data-index="${index}">
        <select class="select txn-split-category" aria-label="Category">${options}</select>
        <input type="number" class="input txn-split-percent" min="0" max="100" step="1"
          value="${single ? 100 : row.percent}" ${single ? "disabled" : ""} aria-label="Percent" />
        <span class="txn-split-percent-sign">%</span>
        <button type="button" class="btn btn-quiet txn-remove-split" aria-label="Remove this split">×</button>
      </div>
    `;
  }

  function categoriesSectionHtml(tx) {
    const rowsHtml = editorState.catRows.length
      ? editorState.catRows.map((row, i) => catRowHtml(row, i)).join("")
      : '<p class="txn-cat-empty">No category yet.</p>';

    const multi = editorState.catRows.length > 1;
    const sum = sumPercents();
    const allChosen = editorState.catRows.length > 0 && editorState.catRows.every((r) => r.categoryId);
    const canSave = allChosen && (!multi || sum === 100);

    return `
      <div class="txn-section-head">
        <p class="txn-section-title">Category</p>
        ${tx.categories_set_by === "user" ? '<span class="badge txn-set-by">set by you</span>' : ""}
      </div>
      ${rowsHtml}
      <button type="button" class="btn btn-quiet txn-add-split">+ Add a split</button>
      ${multi ? `<p class="txn-split-hint${sum === 100 ? "" : " is-off"}">Splits must total 100% — currently ${sum}%</p>` : ""}
      ${editorState.catError ? `<p class="field-error">${esc(editorState.catError)}</p>` : ""}
      <button type="button" class="btn btn-primary txn-save-categories" ${canSave ? "" : "disabled"}>Save category</button>
    `;
  }

  function rerenderCatSection() {
    const tx = currentEditTx();
    const el = els.tableWrap.querySelector(".txn-cat-section");
    if (el && tx) el.innerHTML = categoriesSectionHtml(tx);
    resizeEditor();
  }

  function updateCatSaveState() {
    const sum = sumPercents();
    const hintEl = els.tableWrap.querySelector(".txn-split-hint");
    if (hintEl) {
      hintEl.textContent = `Splits must total 100% — currently ${sum}%`;
      hintEl.classList.toggle("is-off", sum !== 100);
    }
    const saveBtn = els.tableWrap.querySelector(".txn-save-categories");
    if (saveBtn) {
      const multi = editorState.catRows.length > 1;
      const allChosen = editorState.catRows.length > 0 && editorState.catRows.every((r) => r.categoryId);
      saveBtn.disabled = !allChosen || (multi && sum !== 100);
    }
    resizeEditor();
  }

  function onAddSplit() {
    editorState.catRows.push({ categoryId: "", percent: 0 });
    editorState.catError = null;
    rerenderCatSection();
  }

  function onRemoveSplit(btn) {
    const row = btn.closest(".txn-split-row");
    const index = Number(row.dataset.index);
    editorState.catRows.splice(index, 1);
    editorState.catError = null;
    rerenderCatSection();
  }

  async function onSaveCategories() {
    const tx = currentEditTx();
    if (!tx || !editorState) return;

    const single = editorState.catRows.length === 1;
    const fractions = {};
    for (const row of editorState.catRows) {
      if (!row.categoryId) return; // guarded by disabled button; defensive no-op
      fractions[row.categoryId] = (single ? 100 : row.percent) / 100;
    }

    const btn = els.tableWrap.querySelector(".txn-save-categories");
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Saving…";
    }
    try {
      const updated = await api.patchTransaction(tx.id, { categories: fractions });
      editorState.catError = null;
      applyUpdatedTransaction(updated);
    } catch (err) {
      editorState.catError = err.detail || "Couldn't save that split.";
      rerenderCatSection();
    }
  }

  // -------------------------------- editor: tags --------------------------------

  function tagsSectionHtml(tx) {
    const chips = tags
      .slice()
      .sort(byName)
      .map((t) => {
        const active = editorState.tagIds.has(String(t.id));
        return `<button type="button" class="chip txn-tag-toggle${active ? " chip-accent" : ""}" data-tag-id="${esc(t.id)}">${esc(t.name)}</button>`;
      })
      .join("");

    return `
      <div class="txn-section-head">
        <p class="txn-section-title">Tags</p>
      </div>
      <div class="txn-tag-chips">${chips || '<p class="txn-cat-empty">No tags yet.</p>'}</div>
      <div class="txn-add-tag-row">
        <input type="text" class="input txn-new-tag-input" placeholder="New tag…" aria-label="New tag name" />
        <button type="button" class="btn btn-ghost txn-add-tag-btn">Add</button>
      </div>
      ${editorState.tagError ? `<p class="field-error">${esc(editorState.tagError)}</p>` : ""}
      <button type="button" class="btn btn-primary txn-save-tags">Save tags</button>
    `;
  }

  function rerenderTagSection() {
    const tx = currentEditTx();
    const el = els.tableWrap.querySelector(".txn-tag-section");
    if (el && tx) el.innerHTML = tagsSectionHtml(tx);
    resizeEditor();
  }

  function onToggleTag(el) {
    const id = el.dataset.tagId;
    if (editorState.tagIds.has(id)) {
      editorState.tagIds.delete(id);
      el.classList.remove("chip-accent");
    } else {
      editorState.tagIds.add(id);
      el.classList.add("chip-accent");
    }
  }

  async function onAddTag() {
    if (!editorState) return;
    const input = els.tableWrap.querySelector(".txn-new-tag-input");
    const name = input ? input.value.trim() : "";
    if (!name) return;
    try {
      const created = await api.createTag(name);
      tags.push(created);
      editorState.tagIds.add(String(created.id));
      editorState.tagError = null;
      rerenderTagSection();
    } catch (err) {
      editorState.tagError = err.detail || "Couldn't create that tag.";
      rerenderTagSection();
    }
  }

  async function onSaveTags() {
    const tx = currentEditTx();
    if (!tx || !editorState) return;

    const btn = els.tableWrap.querySelector(".txn-save-tags");
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Saving…";
    }
    try {
      const updated = await api.patchTransaction(tx.id, { tags: Array.from(editorState.tagIds) });
      editorState.tagError = null;
      applyUpdatedTransaction(updated);
    } catch (err) {
      editorState.tagError = err.detail || "Couldn't save those tags.";
      rerenderTagSection();
    }
  }

  // ------------------------------- shared: apply -------------------------------

  function applyUpdatedTransaction(updated) {
    const idx = rows.findIndex((r) => r.id === updated.id);
    if (idx !== -1) rows[idx] = updated;

    const rowTr = findRowTr(updated.id);
    if (rowTr) {
      const catCell = rowTr.querySelector(".txn-cat-cell");
      const tagCell = rowTr.querySelector(".txn-tag-cell");
      if (catCell) catCell.innerHTML = categoryChipsMarkup(updated);
      if (tagCell) tagCell.innerHTML = tagChipsMarkup(updated);
    }

    if (editorState && editorState.txId === updated.id) {
      editorState = buildInitialEditorState(updated);
      rerenderCatSection();
      rerenderTagSection();
    }
  }

  // ------------------------------- delegated events -------------------------------

  function handleTableWrapClick(e) {
    const th = e.target.closest("th.is-sortable");
    if (th) {
      handleSortClick(th.dataset.sort);
      return;
    }

    const retryBtn = e.target.closest(".txn-retry");
    if (retryBtn) {
      loadPage();
      return;
    }

    const addSplit = e.target.closest(".txn-add-split");
    if (addSplit) {
      onAddSplit();
      return;
    }
    const removeSplit = e.target.closest(".txn-remove-split");
    if (removeSplit) {
      onRemoveSplit(removeSplit);
      return;
    }
    const saveCat = e.target.closest(".txn-save-categories");
    if (saveCat) {
      onSaveCategories();
      return;
    }

    const tagToggle = e.target.closest(".txn-tag-toggle");
    if (tagToggle) {
      onToggleTag(tagToggle);
      return;
    }
    const addTagBtn = e.target.closest(".txn-add-tag-btn");
    if (addTagBtn) {
      onAddTag();
      return;
    }
    const saveTags = e.target.closest(".txn-save-tags");
    if (saveTags) {
      onSaveTags();
      return;
    }

    const row = e.target.closest("tr.txn-row");
    if (row) {
      toggleRow(row);
    }
  }

  function handleSortClick(field) {
    if (state.sort === field) {
      state.order = state.order === "asc" ? "desc" : "asc";
    } else {
      state.sort = field;
      state.order = field === "description" ? "asc" : "desc";
    }
    state.offset = 0;
    loadPage();
  }

  function handleTableWrapChange(e) {
    const sel = e.target.closest(".txn-split-category");
    if (sel) {
      const row = sel.closest(".txn-split-row");
      const index = Number(row.dataset.index);
      editorState.catRows[index].categoryId = sel.value;
      editorState.catError = null;
      rerenderCatSection();
    }
  }

  function handleTableWrapInput(e) {
    const percentInput = e.target.closest(".txn-split-percent");
    if (percentInput) {
      const row = percentInput.closest(".txn-split-row");
      const index = Number(row.dataset.index);
      const val = Math.max(0, Math.min(100, Math.round(Number(percentInput.value) || 0)));
      editorState.catRows[index].percent = val;
      updateCatSaveState();
    }
  }

  function handleTableWrapKeydown(e) {
    if (e.key === "Enter" && e.target.classList.contains("txn-new-tag-input")) {
      e.preventDefault();
      onAddTag();
      return;
    }
    if ((e.key === "Enter" || e.key === " ") && e.target.matches("tr.txn-row")) {
      e.preventDefault();
      toggleRow(e.target);
    }
  }
}

// ----------------------------------- helpers -----------------------------------

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
  ));
}

function formatDate(isoDate) {
  if (!isoDate) return "";
  const [y, m, d] = isoDate.split("-").map(Number);
  if (!y || !m || !d) return isoDate;
  const date = new Date(y, m - 1, d);
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}
