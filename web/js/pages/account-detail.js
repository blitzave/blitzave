// BlitZave — single account page (#/accounts/:id). Header with the
// account's own balance, then the shared transactions table scoped to it.
//
// Owned by the accounts agent.

import * as api from "../api.js";
import { renderTransactionsTable } from "../components/transactions-table.js";

const ASSET_LABELS = { checking: "Checking", savings: "Savings" };
const LIABILITY_LABELS = { credit_card: "Credit card", loan: "Loan" };

function subtypeLabel(account) {
  if (account.account_type === "asset") {
    return ASSET_LABELS[account.asset_type] || "Account";
  }
  return LIABILITY_LABELS[account.liability_type] || "Account";
}

function institution(account) {
  return account.bank_name || account.issuer_name || "";
}

// See accounts.js for why this parses as UTC — it's date formatting, not
// money formatting, so it's fine to do here rather than in the API.
function formatAsOf(dateStr) {
  if (!dateStr) return "";
  const iso = dateStr.length === 10 ? `${dateStr}T00:00:00Z` : dateStr;
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return dateStr;
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  }).format(parsed);
}

export async function render(root, params) {
  const id = params && params.id ? params.id : "";
  renderLoading(root);

  let accounts;
  try {
    accounts = await api.listAccounts();
  } catch (err) {
    renderError(root, err, () => render(root, params));
    return;
  }

  const account = accounts.find((a) => a.id === id);
  if (!account) {
    renderNotFound(root);
    return;
  }

  renderDetail(root, account);
}

function renderLoading(root) {
  root.innerHTML = `
    <div class="account-detail-page">
      <a class="link account-detail-back" href="#/accounts">&larr; Accounts</a>
      <div class="skeleton skeleton-text" style="width: 220px; height: 1.4em;"></div>
      <div class="skeleton skeleton-text" style="width: 140px; height: 0.9em; margin-top: 10px;"></div>
      <div class="skeleton" style="width: 200px; height: 2.6em; margin-top: 20px;"></div>
    </div>
  `;
}

function renderError(root, err, retry) {
  root.innerHTML = `
    <div class="empty-state">
      <p class="empty-state-title">Couldn't load this account</p>
      <p class="empty-state-body"></p>
      <button class="btn btn-ghost" type="button" id="account-detail-retry">Try again</button>
      <a class="link" href="#/accounts">&larr; Back to accounts</a>
    </div>
  `;
  root.querySelector(".empty-state-body").textContent =
    (err && err.detail) || "Something went wrong reaching BlitZave.";
  root.querySelector("#account-detail-retry").addEventListener("click", retry);
}

function renderNotFound(root) {
  root.innerHTML = `
    <div class="empty-state">
      <p class="empty-state-title">We couldn't find that account</p>
      <p class="empty-state-body">It may have been recategorized or removed. Head back and pick another one.</p>
      <a class="link" href="#/accounts">&larr; Back to accounts</a>
    </div>
  `;
}

function renderDetail(root, account) {
  root.innerHTML = `
    <div class="account-detail-page">
      <a class="link account-detail-back" href="#/accounts">&larr; Accounts</a>
      <header class="account-detail-header">
        <div>
          <p class="account-detail-name"></p>
          <p class="account-detail-institution"></p>
        </div>
        <span class="badge account-detail-badge"></span>
      </header>
      <p class="account-detail-balance card-figure u-tabular"></p>
      <p class="account-detail-asof"></p>
      <div class="account-detail-transactions" id="account-detail-transactions"></div>
    </div>
  `;

  root.querySelector(".account-detail-name").textContent = account.display_name;
  root.querySelector(".account-detail-institution").textContent = institution(account);
  root.querySelector(".account-detail-badge").textContent = subtypeLabel(account);
  root.querySelector(".account-detail-balance").textContent = account.balance_display;
  root.querySelector(".account-detail-asof").textContent = account.balance_as_of
    ? `as of ${formatAsOf(account.balance_as_of)}`
    : "";

  renderTransactionsTable(root.querySelector("#account-detail-transactions"), {
    accountId: account.id,
  });
}
