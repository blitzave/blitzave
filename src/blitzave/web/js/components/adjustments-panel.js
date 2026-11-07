// BlitZave — adjustments panel.
//
// Every linked pair comes from one list (GET /api/transfers). The server says
// what each pair is (kind: transfer, refund or credit); the panel shows money
// moved between accounts apart from refunds and credits within one account.
// Mounted by the transactions page behind its "Adjustments" toggle.
//
//   const panel = renderAdjustmentsPanel(root, { onUnlinked })
//   panel.reload()  // e.g. after the table links a new pair
//
// Money rule: every figure comes straight from amount_display.

// Share the current adjustment client with the transaction editor, even if
// the shell still holds a client loaded before refunds were added.
import * as api from "../api.js?adjustments=1";

/**
 * @param {HTMLElement} root
 * @param {{onUnlinked?: () => void}} [options]
 */
export function renderAdjustmentsPanel(root, { onUnlinked = null } = {}) {
  let transfers = [];
  let refunds = [];
  let accountNames = new Map(); // raw display_name → friendly account name
  let confirmingId = null; // the pair showing "Unlink? …" inline
  let unlinkingId = null;
  let rowError = null; // { id, message }
  let loadSeq = 0;

  root.addEventListener("click", handleClick);
  load();

  return { reload: load };

  async function load() {
    const seq = ++loadSeq;
    root.innerHTML = `
      <div class="adjustments-skeleton">
        ${Array.from({ length: 2 }).map(() => '<div class="skeleton adjustments-skeleton-row"></div>').join("")}
      </div>
    `;
    try {
      // Legs carry only the raw display_name; the accounts list supplies the
      // friendly name. A failed accounts fetch just falls back to the raw one.
      const [pairs, accts] = await Promise.all([
        api.listAdjustments(),
        api.listAccounts().catch(() => []),
      ]);
      if (seq !== loadSeq) return;
      transfers = (pairs || []).filter((p) => p.kind === "transfer");
      refunds = (pairs || []).filter((p) => p.kind !== "transfer");
      accountNames = new Map((accts || []).map((a) => {
        const name = a.name || a.display_name;
        const lastFour = a.account_number;
        return [a.display_name, lastFour && !name.includes(lastFour) ? `${name} · •••• ${lastFour}` : name];
      }));
      confirmingId = null;
      unlinkingId = null;
      rowError = null;
      renderList();
    } catch (err) {
      if (seq !== loadSeq) return;
      root.innerHTML = `
        <div class="empty-state">
          <p class="empty-state-title">Couldn't load adjustments</p>
          <p class="empty-state-body">${esc(err.detail || "Something went wrong.")}</p>
          <button type="button" class="btn btn-ghost adjustments-retry">Try again</button>
        </div>
      `;
    }
  }

  function renderList() {
    if (transfers.length === 0 && refunds.length === 0) {
      root.innerHTML = `
        <div class="empty-state">
          <p class="empty-state-title">No adjustments yet</p>
          <p class="empty-state-body">Open a transaction to link a transfer between accounts, or match a refund or credit to its charge in the same account.</p>
        </div>
      `;
      return;
    }
    root.innerHTML = `
      <section class="adjustments-group">
        <h2 class="adjustments-group-title">Transfers between accounts</h2>
        ${transfers.length
          ? `<ul class="adjustments-list">${transfers.map((t) => itemHtml(t, "transfer")).join("")}</ul>`
          : '<p class="txn-adjustment-hint">No linked transfers yet.</p>'}
      </section>
      <section class="adjustments-group">
        <h2 class="adjustments-group-title">Refunds / credits</h2>
        ${refunds.length
          ? `<ul class="adjustments-list">${refunds.map((r) => itemHtml(r, "refund")).join("")}</ul>`
          : '<p class="txn-adjustment-hint">No linked refunds or credits yet.</p>'}
      </section>
    `;
  }

  function legHtml(label, leg) {
    return `
      <div class="adjustment-leg">
        <span class="adjustment-leg-label">${label}</span>
        <span class="adjustment-leg-account">${esc(accountNames.get(leg.account_display_name) || leg.account_display_name || "")}</span>
        <span class="adjustment-leg-desc">${esc(leg.description || "")}</span>
        <span class="adjustment-leg-desc u-tabular">${esc(leg.amount_display)}</span>
      </div>
    `;
  }

  function itemHtml(t, type) {
    const id = String(t.id);
    const key = id;
    const refund = type === "refund";
    const kindLabel = { transfer: "Transfer", refund: "Refund", credit: "Credit" }[t.kind] || "";
    // The sync links pairs on its own; say so, so they're easy to review.
    const source = t.matched_by === "rule" ? " · linked automatically" : "";
    let actions;
    if (unlinkingId === key) {
      actions = '<span class="adjustment-unlink-prompt">Unlinking…</span>';
    } else if (confirmingId === key) {
      actions = `
        <span class="adjustment-unlink-prompt">They'll count as spending or income again, and won't be linked automatically.</span>
        <button type="button" class="btn btn-ghost adjustment-unlink-confirm" data-id="${esc(id)}">Unlink</button>
        <button type="button" class="btn btn-quiet adjustment-unlink-cancel">Keep</button>
      `;
    } else {
      actions = `<button type="button" class="btn btn-quiet adjustment-unlink" data-id="${esc(id)}">Unlink</button>`;
    }
    const error = rowError && rowError.key === key ? `<p class="field-error">${esc(rowError.message)}</p>` : "";
    return `
      <li class="adjustment-item">
        <div class="adjustment-legs">
          ${legHtml(refund ? "Charge" : "From", t.debit)}
          <svg class="adjustment-arrow" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6" /></svg>
          ${legHtml(refund ? "Credit" : "To", t.credit)}
        </div>
        <div class="adjustment-meta">
          <span class="adjustment-amount u-tabular">${esc(t.amount_display)}</span>
          <span class="adjustment-date u-tabular">${esc(formatDate(t.transfer_date))}</span>
          <span class="adjustment-date">${esc(kindLabel + source)}</span>
        </div>
        <div class="adjustment-actions">${actions}</div>
        ${error}
      </li>
    `;
  }

  async function unlink(id) {
    const key = id;
    unlinkingId = key;
    rowError = null;
    renderList();
    try {
      await api.deleteAdjustment(id);
      if (onUnlinked) onUnlinked();
      await load();
    } catch (err) {
      unlinkingId = null;
      confirmingId = null;
      rowError = { key, message: err.detail || "Couldn't unlink that pair." };
      renderList();
    }
  }

  function handleClick(e) {
    if (e.target.closest(".adjustments-retry")) {
      load();
      return;
    }
    const start = e.target.closest(".adjustment-unlink");
    if (start) {
      confirmingId = start.dataset.id;
      rowError = null;
      renderList();
      return;
    }
    if (e.target.closest(".adjustment-unlink-cancel")) {
      confirmingId = null;
      renderList();
      return;
    }
    const confirmBtn = e.target.closest(".adjustment-unlink-confirm");
    if (confirmBtn) unlink(confirmBtn.dataset.id);
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
