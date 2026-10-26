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
// Optional `onAdjustmentsChanged` is called after any adjustment is linked or unlinked.
// The returned `{ reload }` lets a page refresh the list (e.g. after an unlink).
//
// Money rule: every figure a person reads comes straight from amount_display.
// amount_cents is only ever used to decide credit/debit color — never shown.

// Keep the adjustment API separate from a pre-adjustments client that the
// shell may already have loaded before this lazy-loaded component.
import * as api from "../api.js?adjustments=1";

const PAGE_SIZE = 50;
const AVATAR_TINTS = 6; // matches the .txn-avatar-0 … -5 classes in transactions.css

// Small SF-Symbols-energy line icons, 24px viewBox, currentColor stroke —
// sized/colored by the matching class in transactions.css. Same pattern as
// main.js's ICON_* constants: no icon fonts, every glyph is inline markup.
const ICON_SEARCH = `
  <svg class="txn-search-icon" viewBox="0 0 24 24" aria-hidden="true">
    <circle cx="10.5" cy="10.5" r="6.5" />
    <path d="M20 20l-4.8-4.8" />
  </svg>
`;

const ICON_PLUS = `
  <svg class="txn-plus-icon" viewBox="0 0 24 24" aria-hidden="true">
    <path d="M12 5v14M5 12h14" />
  </svg>
`;

const ICON_CHECK = `
  <svg class="txns-notice-icon" viewBox="0 0 24 24" aria-hidden="true">
    <circle cx="12" cy="12" r="9" />
    <path d="M8 12.5l2.7 2.7L16 9.8" />
  </svg>
`;

const ICON_CLOSE = `
  <svg class="txn-close-icon" viewBox="0 0 24 24" aria-hidden="true">
    <path d="M6 6l12 12M18 6L6 18" />
  </svg>
`;

const ICON_CHEVRON_LEFT = `
  <svg class="txns-page-icon" viewBox="0 0 24 24" aria-hidden="true">
    <path d="M15 5l-7 7 7 7" />
  </svg>
`;

const ICON_CHEVRON_RIGHT = `
  <svg class="txns-page-icon" viewBox="0 0 24 24" aria-hidden="true">
    <path d="M9 5l7 7-7 7" />
  </svg>
`;

// Money moved between two of your accounts.
const ICON_TRANSFER = `
  <svg class="txn-kind-icon" viewBox="0 0 24 24" aria-hidden="true">
    <path d="M4 8h14l-4-4M20 16H6l4 4" />
  </svg>
`;

// Money that came back to the account it left.
const ICON_REFUND = `
  <svg class="txn-kind-icon" viewBox="0 0 24 24" aria-hidden="true">
    <path d="M9 14L4 9l5-5" />
    <path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11" />
  </svg>
`;

const ICON_GROUP_CHEVRON = `
  <svg class="txn-group-chevron" viewBox="0 0 24 24" aria-hidden="true">
    <path d="M6 9l6 6 6-6" />
  </svg>
`;

// Visible tags (not quiet labels) for a transaction that's linked to its
// other side — the user should never have to hunt for this.
const KIND_CHIPS = {
  transfer: `<span class="chip txn-kind-chip txn-kind-transfer">${ICON_TRANSFER}Transfer</span>`,
  refund: `<span class="chip txn-kind-chip txn-kind-refund">${ICON_REFUND}Refund / credit</span>`,
};

/**
 * @param {HTMLElement} root
 * @param {{accountId?: string|null, onAdjustmentsChanged?: () => void}} [options]
 */
export function renderTransactionsTable(root, { accountId = null, onAdjustmentsChanged = null } = {}) {
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
  let editorState = null; // { txId, catRows: [{categoryId, percent}], applyToAll, tagIds: Set, catError, tagError, adjustment }
  const expandedGroups = new Set(); // ids of refund-pair tiles currently showing their two transactions

  let loadSeq = 0;
  let searchDebounce = null;

  const els = {};

  mountShell();
  init();

  return { reload: () => loadPage() };

  // --------------------------------- shell ---------------------------------

  function mountShell() {
    root.innerHTML = `
      <div class="txns-filterbar"></div>
      <div class="txns-notice" role="status" hidden>
        ${ICON_CHECK}
        <p class="txns-notice-text"></p>
        <button type="button" class="btn btn-quiet txns-notice-dismiss" aria-label="Dismiss">${ICON_CLOSE}</button>
      </div>
      <div class="card txns-table-wrap"></div>
      <div class="txns-pagination"></div>
    `;
    els.filterBar = root.querySelector(".txns-filterbar");
    els.notice = root.querySelector(".txns-notice");
    els.tableWrap = root.querySelector(".txns-table-wrap");
    els.pagination = root.querySelector(".txns-pagination");

    els.notice.querySelector(".txns-notice-dismiss").addEventListener("click", clearNotice);

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
        api.listAccounts(),
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

  // Rows and transfer candidates carry the account's raw display_name; show
  // its friendly name and last four digits when the accounts list knows them.
  function accountLabel(displayName) {
    const match = accounts.find((a) => a.display_name === displayName);
    const name = (match && match.name) || displayName || "";
    const lastFour = match && match.account_number;
    return lastFour && !name.includes(lastFour) ? `${name} · •••• ${lastFour}` : name;
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
          ${accounts.slice().sort(byName).map((a) => optionHtml(a.id, a.name || a.display_name, state.accountId)).join("")}
        </select>
      `;

    els.filterBar.innerHTML = `
      <div class="txn-filter-search">
        ${ICON_SEARCH}
        <input type="text" class="input txn-search" placeholder="Search transactions"
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
      // The server collapses each refund pair into one item before sorting
      // and paging, so a pair can never split across pages.
      group_refunds: true,
    };
  }

  // A calm one-line confirmation above the table. It survives the reload that
  // usually follows the action it confirms, and clears on the next one.
  function showNotice(text) {
    els.notice.querySelector(".txns-notice-text").textContent = text;
    els.notice.hidden = false;
  }

  function clearNotice() {
    els.notice.hidden = true;
  }

  async function loadPage({ keepNotice = false } = {}) {
    const seq = ++loadSeq;
    if (!keepNotice) clearNotice();
    expandedId = null;
    editorState = null;
    expandedGroups.clear();
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

  // Active sort only — unsorted headers stay bare, no glyph. The icon
  // rotates 180° for ascending rather than swapping to a second glyph.
  function sortedClass(field) {
    return state.sort === field ? "is-sorted" : "";
  }

  function sortIconHtml(field) {
    if (state.sort !== field) return "";
    const ascClass = state.order === "asc" ? " is-asc" : "";
    return `<svg class="txn-sort-icon${ascClass}" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9l6 6 6-6" /></svg>`;
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
            <th class="is-sortable ${sortedClass("date")}" data-sort="date">Date${sortIconHtml("date")}</th>
            <th class="is-sortable ${sortedClass("description")}" data-sort="description">Description${sortIconHtml("description")}</th>
            <th>Category</th>
            <th class="is-sortable is-numeric ${sortedClass("amount")}" data-sort="amount">Amount${sortIconHtml("amount")}</th>
          </tr>
        </thead>
        <tbody>${rows.map(itemHtml).join("")}</tbody>
      </table>
    `;
  }

  // A 36px circular monogram, tinted by the first letter's charcode across
  // a small fixed set of muted, cool-toned pastels — purely decorative
  // identity color (like a contacts-app avatar), never status: it's a hash
  // of the merchant name, so it carries no judgment about the transaction.

  function avatarHtml(label) {
    const trimmed = (label || "").trim();
    const letter = trimmed ? trimmed.charAt(0).toUpperCase() : "?";
    const code = trimmed ? trimmed.charCodeAt(0) : 0;
    const tint = code % AVATAR_TINTS;
    return `<span class="txn-avatar txn-avatar-${tint}" aria-hidden="true">${esc(letter)}</span>`;
  }

  function itemHtml(item) {
    if (item.kind !== "refund_group") return rowHtml(item);
    const open = expandedGroups.has(item.id);
    const legs = open ? item.legs.map((leg) => rowHtml(leg, { groupId: item.id })).join("") : "";
    return groupRowHtml(item, open) + legs;
  }

  // A linked charge and its refund/credit, shown as one tile that nets to
  // zero. The two real transactions sit one click down, still editable.
  function groupRowHtml(group, open) {
    // A full refund nets to zero and reads quiet; a partial one still owes
    // the difference and reads like any other debit.
    const netClass = group.amount_cents === 0
      ? "txn-amount-zero"
      : group.amount_cents > 0 ? "txn-amount-credit" : "txn-amount-debit";
    return `
      <tr class="txn-group-row${open ? " is-open" : ""}" data-group-id="${esc(group.id)}"
        tabindex="0" role="button" aria-expanded="${open ? "true" : "false"}"
        aria-label="Refund pair: ${esc(group.description)}. ${open ? "Hide" : "Show"} both transactions">
        <td class="txn-date-cell u-tabular">${esc(formatDate(group.posted_on))}</td>
        <td class="txn-desc-cell">
          <div class="txn-desc-row">
            <span class="txn-avatar txn-avatar-refund" aria-hidden="true">${ICON_REFUND}</span>
            <div class="txn-desc-text">
              <div class="txn-desc">${esc(group.description)}</div>
              <div class="txn-account">${esc(accountLabel(group.account_display_name))} · 2 transactions</div>
            </div>
          </div>
        </td>
        <td><div class="txn-chip-cell">${KIND_CHIPS.refund}</div></td>
        <td class="is-numeric u-tabular txn-amount-group ${netClass}">
          <span class="txn-net-figure">${esc(group.amount_display)}</span>${ICON_GROUP_CHEVRON}
        </td>
      </tr>
    `;
  }

  function rowHtml(tx, { groupId = null } = {}) {
    const amountClass = tx.amount_cents > 0 ? "txn-amount-credit" : "txn-amount-debit";
    const identityLabel = tx.merchant_name || tx.description || "";
    const merchantHtml =
      tx.merchant_name && tx.merchant_name !== tx.description
        ? `<div class="txn-merchant">${esc(tx.merchant_name)}</div>`
        : "";
    const legAttrs = groupId ? ` data-group-id="${esc(groupId)}"` : "";
    return `
      <tr class="txn-row${groupId ? " txn-leg-row" : ""}" data-id="${esc(tx.id)}"${legAttrs} tabindex="0" role="button" aria-expanded="false">
        <td class="txn-date-cell u-tabular">${esc(formatDate(tx.posted_on))}</td>
        <td class="txn-desc-cell">
          <div class="txn-desc-row">
            ${avatarHtml(identityLabel)}
            <div class="txn-desc-text">
              <div class="txn-desc">${esc(tx.description || "")}</div>
              ${merchantHtml}
              <div class="txn-account">${esc(accountLabel(tx.account_display_name))}</div>
            </div>
          </div>
        </td>
        <td>
          <div class="txn-chip-cell">
            <span class="txn-cat-cell">${categoryChipsMarkup(tx)}</span>
            <span class="txn-tag-cell">${tagChipsMarkup(tx)}</span>
          </div>
        </td>
        <td class="is-numeric u-tabular ${amountClass}">${esc(tx.amount_display)}</td>
      </tr>
    `;
  }

  function categoryChipsMarkup(tx) {
    // A linked transfer leg isn't missing a category — it's just not spending
    // or income, so its tag stands in for the category chips.
    if (tx.transfer_id != null) {
      return KIND_CHIPS.transfer;
    }
    const refundLabel = tx.refund_id != null ? KIND_CHIPS.refund : "";
    const entries = Object.entries(tx.categories || {});
    if (entries.length === 0) {
      return `<span class="txn-uncategorized">Uncategorized</span>${refundLabel}`;
    }
    return entries
      .map(([catId, fraction]) => {
        const cat = categories.find((c) => String(c.id) === String(catId));
        const name = cat ? cat.name : "Unknown category";
        const pct = Math.round(fraction * 100);
        const label = entries.length > 1 ? `${esc(name)} ${pct}%` : esc(name);
        return `<span class="chip">${label}</span>`;
      })
      .join("") + refundLabel;
  }

  function tagChipsMarkup(tx) {
    if (!tx.tags || tx.tags.length === 0) return "";
    return tx.tags.map((t) => `<span class="chip txn-tag-chip">${esc(t.name)}</span>`).join("");
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
        <button type="button" class="btn btn-ghost txns-page-btn txn-prev" aria-label="Previous page" ${state.offset === 0 ? "disabled" : ""}>${ICON_CHEVRON_LEFT}</button>
        <button type="button" class="btn btn-ghost txns-page-btn txn-next" aria-label="Next page" ${to >= total ? "disabled" : ""}>${ICON_CHEVRON_RIGHT}</button>
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

  // A refund tile's legs are real transactions: the editor and in-place
  // updates must find them as readily as top-level rows. The tile itself
  // isn't a transaction and is never returned.
  function findTx(id) {
    for (const item of rows) {
      if (item.kind === "refund_group") {
        const leg = item.legs.find((l) => l.id === id);
        if (leg) return leg;
      } else if (item.id === id) {
        return item;
      }
    }
    return null;
  }

  function toggleGroup(tr) {
    const id = tr.dataset.groupId;
    const group = rows.find((r) => r.id === id);
    if (!group) return;
    const open = !expandedGroups.has(id);
    if (open) {
      expandedGroups.add(id);
      tr.insertAdjacentHTML("afterend", group.legs.map((leg) => rowHtml(leg, { groupId: id })).join(""));
    } else {
      // close a leg's editor before its row goes away
      if (expandedId && group.legs.some((l) => l.id === expandedId)) collapseRow();
      expandedGroups.delete(id);
      els.tableWrap
        .querySelectorAll("tr.txn-leg-row")
        .forEach((legTr) => { if (legTr.dataset.groupId === id) legTr.remove(); });
    }
    tr.classList.toggle("is-open", open);
    tr.setAttribute("aria-expanded", open ? "true" : "false");
  }

  function expandRow(tr, id) {
    const tx = findTx(id);
    if (!tx) return;

    expandedId = id;
    editorState = buildInitialEditorState(tx);

    tr.classList.add("is-expanded");
    tr.setAttribute("aria-expanded", "true");

    const editorTr = document.createElement("tr");
    editorTr.className = "txn-editor-row";
    editorTr.dataset.id = id;
    editorTr.innerHTML = `<td colspan="4"><div class="txn-editor-wrap"><div class="txn-editor">${editorPanelHtml(tx)}</div></div></td>`;
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
    return findTx(editorState.txId);
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
      applyToAll: false, // only offered while there's exactly one category
      // Stored as strings: dataset.tagId (used to toggle) is always a string,
      // while t.id from the API is numeric — normalize once here so
      // .has()/.add()/.delete() compare like-for-like everywhere below.
      tagIds: new Set((tx.tags || []).map((t) => String(t.id))),
      catError: null,
      tagError: null,
      // status: idle → loading → ready (candidates listed) | error
      adjustment: { type: null, status: "idle", candidates: [], selectedId: null, error: null, saving: false },
      // for a row that's already part of an adjustment: inline Unlink confirm
      unlink: { confirming: false, busy: false, error: null },
    };
  }

  function editorPanelHtml(tx) {
    // A transfer leg has no category to edit (it isn't spending or income),
    // so the category editor is left out and the transfer section offers Unlink.
    const catSection =
      tx.transfer_id != null
        ? ""
        : `<div class="txn-editor-section txn-cat-section">${categoriesSectionHtml(tx)}</div>`;
    return `
      ${catSection}
      <div class="txn-editor-section txn-tag-section">${tagsSectionHtml(tx)}</div>
      <div class="txn-editor-section txn-adjustment-section">${adjustmentSectionHtml(tx)}</div>
      <p class="txn-editor-reminder">Amount, date, and description come from your bank — they can't be edited here.</p>
    `;
  }

  // ------------------------------ editor: categories ------------------------------

  function sumPercents() {
    return editorState.catRows.reduce((acc, r) => acc + (Number(r.percent) || 0), 0);
  }

  function splitHintText(sum) {
    return sum === 100 ? "Splits total 100%." : `Splits must total 100% — currently ${sum}%`;
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
        <button type="button" class="btn btn-quiet txn-remove-split" aria-label="Remove this split">${ICON_CLOSE}</button>
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
      <button type="button" class="btn btn-quiet txn-add-split">${ICON_PLUS}<span>${editorState.catRows.length ? "Add a split" : "Choose a category"}</span></button>
      ${multi ? `<p class="txn-split-hint${sum === 100 ? "" : " is-off"}">${splitHintText(sum)}</p>` : ""}
      ${scopeChoiceHtml(tx)}
      ${editorState.catError ? `<p class="field-error">${esc(editorState.catError)}</p>` : ""}
      <button type="button" class="btn btn-primary txn-save-categories" ${canSave ? "" : "disabled"}>Save category</button>
    `;
  }

  // "Just this one" vs "all transactions like …". Only for a single
  // category — a split is specific to one transaction, so no choice shown.
  function scopeChoiceHtml(tx) {
    const single = editorState.catRows.length === 1 && editorState.catRows[0].categoryId;
    if (!single || !tx.description) return "";
    const name = `txn-scope-${tx.id}`;
    return `
      <fieldset class="txn-scope">
        <legend class="sr-only">Apply this category to</legend>
        <label class="txn-scope-option">
          <input type="radio" class="txn-scope-input" name="${esc(name)}" value="one" ${editorState.applyToAll ? "" : "checked"} />
          <span>Just this one</span>
        </label>
        <label class="txn-scope-option">
          <input type="radio" class="txn-scope-input" name="${esc(name)}" value="all" ${editorState.applyToAll ? "checked" : ""} />
          <span>All transactions like “${esc(tx.description)}”</span>
        </label>
        ${
          editorState.applyToAll
            ? '<p class="txn-scope-hint">Matches the same merchant, ignoring store numbers, reference codes, prefixes like Apple Pay, and small typos. Ones that sync later will follow too.</p>'
            : ""
        }
      </fieldset>
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
      hintEl.textContent = splitHintText(sum);
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
    const applyToAll = single && editorState.applyToAll;
    const body = applyToAll ? { categories: fractions, apply_to_all: true } : { categories: fractions };
    try {
      const updated = await api.patchTransaction(tx.id, body);
      if (applyToAll) {
        // Other rows on this page may have changed too — reload so they show it.
        showNotice(appliedToAllMessage(updated.applied_to_count || 0));
        loadPage({ keepNotice: true });
        return;
      }
      editorState.catError = null;
      applyUpdatedTransaction(updated);
    } catch (err) {
      editorState.catError = categoryErrorMessage(err);
      if (err.status === 422) await dropStaleCategories();
      rerenderCatSection();
    }
  }

  // The API's 422 text ("unknown category ids: [4]") is validation jargon;
  // say what happened in plain words instead.
  function categoryErrorMessage(err) {
    if (err.status === 422 && /unknown category/i.test(err.detail || "")) {
      return "One of those categories was removed, so it can't be applied. Pick another and try again.";
    }
    return err.detail || "Couldn't save that split.";
  }

  // A category deleted elsewhere is still in this component's list — refresh
  // it and clear any selection that no longer exists so a retry can succeed.
  async function dropStaleCategories() {
    try {
      categories = (await api.listCategories()) || [];
    } catch {
      return;
    }
    if (!editorState) return;
    const known = new Set(categories.map((c) => String(c.id)));
    for (const row of editorState.catRows) {
      if (!known.has(String(row.categoryId))) row.categoryId = "";
    }
  }

  function appliedToAllMessage(count) {
    if (count === 0) return "Done — future ones will follow.";
    const noun = count === 1 ? "other transaction" : "other transactions";
    return `Done — ${count} ${noun} updated, and future ones will follow.`;
  }

  // ------------------------------ editor: adjustment ------------------------------

  function adjustmentCandidateHtml(c) {
    const selected = String(editorState.adjustment.selectedId) === String(c.id);
    return `
      <button type="button" class="txn-adjustment-candidate${selected ? " is-selected" : ""}"
        data-candidate-id="${esc(c.id)}" aria-pressed="${selected ? "true" : "false"}">
        <span class="txn-adjustment-cand-date u-tabular">${esc(formatDate(c.posted_on))}</span>
        <span class="txn-adjustment-cand-main">
          <span class="txn-adjustment-cand-account">${esc(accountLabel(c.account_display_name))}</span>
          <span class="txn-adjustment-cand-desc">${esc(c.description || "")}</span>
        </span>
        ${KIND_CHIPS[c.linkKind]}
        <span class="txn-adjustment-cand-amount u-tabular">${esc(c.amount_display)}</span>
      </button>
    `;
  }

  function linkedAdjustmentHtml(tx) {
    const u = editorState.unlink;
    const refund = tx.refund_id != null;
    let actions;
    if (u.busy) {
      actions = '<span class="txn-adjustment-hint">Unlinking…</span>';
    } else if (u.confirming) {
      actions = `
        <span class="txn-adjustment-hint">${refund ? "Unlink this refund pair? Amounts and categories stay as they are." : "It'll count as spending or income again."}</span>
        <button type="button" class="btn btn-ghost txn-unlink-confirm">Unlink</button>
        <button type="button" class="btn btn-quiet txn-unlink-cancel">Keep</button>
      `;
    } else {
      actions = '<button type="button" class="btn btn-quiet txn-unlink-start">Unlink</button>';
    }
    return `
      <p class="txn-adjustment-hint">${refund
        ? "Linked as a refund / credit in this account. Use the same category on the charge and credit so they offset."
        : "Part of a transfer between your accounts — not counted in spending or income."}</p>
      <div class="txn-adjustment-actions">${actions}</div>
      ${u.error ? `<p class="field-error">${esc(u.error)}</p>` : ""}
    `;
  }

  // One flow for both kinds: list what exactly cancels this transaction out,
  // and let the account decide what a match becomes — the same account means
  // a refund / credit, another of your accounts means a transfer.
  function adjustmentSectionHtml(tx) {
    const t = editorState.adjustment;
    const linked = tx && (tx.transfer_id != null || tx.refund_id != null);
    // tx.amount_display verbatim — the UI never derives a figure like "+$14.02".
    const cancels = tx ? `what cancels out ${esc(tx.amount_display)}` : "the other side";
    let body = "";
    if (linked) {
      body = linkedAdjustmentHtml(tx);
    } else if (t.status === "idle") {
      body = `
        <p class="txn-adjustment-hint">A card payment, a transfer, or a refund? Link it to its other side — the same amount moving the opposite way — so it's never counted twice.</p>
        <div class="txn-adjustment-actions">
          <button type="button" class="btn btn-ghost txn-adjustment-start">${ICON_TRANSFER}Find its other side</button>
        </div>
      `;
    } else if (t.status === "loading") {
      body = `<p class="txn-adjustment-hint">Looking for ${cancels}…</p>`;
    } else if (t.status === "error") {
      body = `
        <p class="field-error">${esc(t.error)}</p>
        <button type="button" class="btn btn-quiet txn-adjustment-cancel">Never mind</button>
      `;
    } else if (t.candidates.length === 0) {
      body = `
        <p class="txn-adjustment-hint">Nothing matches ${cancels} yet. The other side may not have posted, or that account isn't linked.</p>
        <button type="button" class="btn btn-quiet txn-adjustment-cancel">Never mind</button>
      `;
    } else {
      const choice = t.candidates.find((c) => String(c.id) === String(t.selectedId));
      const linkLabel = t.saving
        ? "Linking…"
        : !choice
          ? "Choose one above"
          : choice.linkKind === "refund" ? "Link as refund / credit" : "Link as transfer";
      const count = t.candidates.length === 1 ? "One match" : `${t.candidates.length} matches`;
      body = `
        <p class="txn-adjustment-hint">${count} for ${cancels}, closest date first. In this account it's a refund / credit; in another of your accounts, a transfer.</p>
        <div class="txn-adjustment-candidates">${t.candidates.map(adjustmentCandidateHtml).join("")}</div>
        ${t.error ? `<p class="field-error">${esc(t.error)}</p>` : ""}
        <div class="txn-adjustment-actions">
          <button type="button" class="btn btn-primary txn-adjustment-link" ${choice && !t.saving ? "" : "disabled"}>${linkLabel}</button>
          <button type="button" class="btn btn-quiet txn-adjustment-cancel" ${t.saving ? "disabled" : ""}>Never mind</button>
        </div>
      `;
    }
    const title = linked
      ? tx.refund_id != null ? "Refund / credit" : "Transfer"
      : "Link to its other side";
    return `
      <div class="txn-section-head">
        <p class="txn-section-title">${title}</p>
      </div>
      ${body}
    `;
  }

  function rerenderAdjustmentSection() {
    const el = els.tableWrap.querySelector(".txn-adjustment-section");
    if (el && editorState) el.innerHTML = adjustmentSectionHtml(currentEditTx());
    resizeEditor();
  }

  async function onAdjustmentStart() {
    const tx = currentEditTx();
    if (!tx) return;
    const txId = tx.id;
    const picker = { type: null, status: "loading", candidates: [], selectedId: null, error: null, saving: false };
    editorState.adjustment = picker;
    rerenderAdjustmentSection();
    try {
      // Both lists hold only the exact opposite amount (the server enforces
      // it); merging two short lists here is cheaper than another endpoint.
      const [transferMatches, refundMatches] = await Promise.all([
        api.adjustmentCandidates(txId),
        api.refundCandidates(txId),
      ]);
      if (!editorState || editorState.adjustment !== picker) return; // picker closed meanwhile
      picker.status = "ready";
      picker.candidates = [
        ...(transferMatches || []).map((c) => ({ ...c, linkKind: "transfer" })),
        ...(refundMatches || []).map((c) => ({ ...c, linkKind: "refund" })),
      ].sort((a, b) => daysApart(a.posted_on, tx.posted_on) - daysApart(b.posted_on, tx.posted_on));
    } catch (err) {
      if (!editorState || editorState.adjustment !== picker) return;
      picker.status = "error";
      picker.error = err.detail || "Couldn't look for the other side.";
    }
    rerenderAdjustmentSection();
  }

  function onAdjustmentCancel() {
    editorState.adjustment = { type: null, status: "idle", candidates: [], selectedId: null, error: null, saving: false };
    rerenderAdjustmentSection();
  }

  function onPickCandidate(el) {
    editorState.adjustment.selectedId = el.dataset.candidateId;
    editorState.adjustment.error = null;
    rerenderAdjustmentSection();
  }

  async function onAdjustmentLink() {
    const tx = currentEditTx();
    const t = editorState && editorState.adjustment;
    const choice = t && t.candidates.find((c) => String(c.id) === String(t.selectedId));
    if (!tx || !choice || t.saving) return;
    t.saving = true;
    rerenderAdjustmentSection();
    try {
      if (choice.linkKind === "refund") {
        await api.createRefund(tx.id, choice.id);
        showNotice("Linked as a refund / credit — the pair now shows as one tile that nets to zero.");
      } else {
        await api.createAdjustment(tx.id, choice.id);
        showNotice("Marked as a transfer. It stays in each account's list but isn't counted in spending or income.");
      }
      loadPage({ keepNotice: true });
      if (onAdjustmentsChanged) onAdjustmentsChanged();
    } catch (err) {
      if (!editorState || editorState.txId !== tx.id) return;
      t.saving = false;
      t.error = err.detail || "Couldn't link those two.";
      rerenderAdjustmentSection();
    }
  }

  function setUnlink(patch) {
    Object.assign(editorState.unlink, patch);
    rerenderAdjustmentSection();
  }

  async function onUnlinkConfirm() {
    const tx = currentEditTx();
    if (!tx || (tx.transfer_id == null && tx.refund_id == null) || editorState.unlink.busy) return;
    setUnlink({ busy: true, error: null });
    try {
      if (tx.refund_id != null) {
        await api.deleteRefund(tx.refund_id);
        showNotice("Refund pair unlinked. Amounts and categories stay as they are.");
      } else {
        await api.deleteAdjustment(tx.transfer_id);
        showNotice("Unlinked. Both sides count as spending or income again.");
      }
      loadPage({ keepNotice: true });
      if (onAdjustmentsChanged) onAdjustmentsChanged();
    } catch (err) {
      if (!editorState || editorState.txId !== tx.id) return;
      setUnlink({ busy: false, confirming: false, error: err.detail || "Couldn't unlink that pair." });
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
        <button type="button" class="btn btn-ghost txn-add-tag-btn">${ICON_PLUS}<span>Add</span></button>
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
    // Merge rather than replace: keeps list-only fields (like `kind`) on a
    // top-level row, and a refund tile's leg is updated where it lives.
    for (const item of rows) {
      if (item.kind === "refund_group") {
        const legIdx = item.legs.findIndex((l) => l.id === updated.id);
        if (legIdx !== -1) item.legs[legIdx] = { ...item.legs[legIdx], ...updated };
      } else if (item.id === updated.id) {
        Object.assign(item, updated);
      }
    }

    const rowTr = findRowTr(updated.id);
    if (rowTr) {
      const catCell = rowTr.querySelector(".txn-cat-cell");
      const tagCell = rowTr.querySelector(".txn-tag-cell");
      if (catCell) catCell.innerHTML = categoryChipsMarkup(updated);
      if (tagCell) tagCell.innerHTML = tagChipsMarkup(updated);
    }

    if (editorState && editorState.txId === updated.id) {
      // a category/tag save shouldn't reset an open transfer picker or unlink confirm
      const { adjustment, unlink } = editorState;
      editorState = buildInitialEditorState(updated);
      editorState.adjustment = adjustment;
      editorState.unlink = unlink;
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

    if (e.target.closest(".txn-adjustment-start")) {
      onAdjustmentStart();
      return;
    }
    if (e.target.closest(".txn-adjustment-cancel")) {
      onAdjustmentCancel();
      return;
    }
    const candidate = e.target.closest(".txn-adjustment-candidate");
    if (candidate) {
      onPickCandidate(candidate);
      return;
    }
    if (e.target.closest(".txn-adjustment-link")) {
      onAdjustmentLink();
      return;
    }
    if (e.target.closest(".txn-unlink-start")) {
      setUnlink({ confirming: true, error: null });
      return;
    }
    if (e.target.closest(".txn-unlink-cancel")) {
      setUnlink({ confirming: false });
      return;
    }
    if (e.target.closest(".txn-unlink-confirm")) {
      onUnlinkConfirm();
      return;
    }

    const groupRow = e.target.closest("tr.txn-group-row");
    if (groupRow) {
      toggleGroup(groupRow);
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
    const scope = e.target.closest(".txn-scope-input");
    if (scope) {
      editorState.applyToAll = scope.value === "all";
      rerenderCatSection();
      return;
    }
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
    if ((e.key === "Enter" || e.key === " ") && e.target.matches("tr.txn-group-row")) {
      e.preventDefault();
      toggleGroup(e.target);
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

// Calendar distance for "closest date first" — date math, not money math.
function daysApart(isoA, isoB) {
  return Math.abs(Date.parse(isoA) - Date.parse(isoB));
}

function formatDate(isoDate) {
  if (!isoDate) return "";
  const [y, m, d] = isoDate.split("-").map(Number);
  if (!y || !m || !d) return isoDate;
  const date = new Date(y, m - 1, d);
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}
