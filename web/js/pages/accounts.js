// BlitZave — accounts overview page (#/accounts, also the default landing
// page). Calm home: two groups — Assets and Cards — each a grid of account
// cards. No net-worth math here; there is no total endpoint, so we never
// invent one.
//
// This is the signature screen of the whole app: liability (credit card)
// accounts render as Apple-Wallet-style dark cards; asset accounts stay
// plain white cards with the same bold-ink balance treatment. The contrast
// between the two faces is the point — see accounts.css.
//
// Owned by the accounts agent.

import * as api from "../api.js";
import { navigate } from "../router.js";

const ASSET_LABELS = { checking: "Checking", savings: "Savings" };
const LIABILITY_LABELS = { credit_card: "Credit card", loan: "Loan" };

// Real inline SVG, 16-20px, currentColor, round caps — see DESIGN.md's
// iconography rule. Same dot pattern the categories page uses, for a
// consistent ellipsis across the app.
const ICON_ELLIPSIS = `
  <svg class="account-card-menu-icon" viewBox="0 0 24 24" aria-hidden="true">
    <circle cx="5" cy="12" r="1.8" fill="currentColor" stroke="none" />
    <circle cx="12" cy="12" r="1.8" fill="currentColor" stroke="none" />
    <circle cx="19" cy="12" r="1.8" fill="currentColor" stroke="none" />
  </svg>
`;

function subtypeLabel(account) {
  if (account.account_type === "asset") {
    return ASSET_LABELS[account.asset_type] || "Account";
  }
  return LIABILITY_LABELS[account.liability_type] || "Account";
}

// Reclassifying a card as an asset makes the API echo the account's own name
// back as its bank name — don't show the same line twice.
function institution(account) {
  const label = account.bank_name || account.issuer_name || "";
  return label === account.name ? "" : label;
}

// balance_as_of is a date (sometimes date-only "YYYY-MM-DD"). Parse as UTC
// so a date-only string doesn't shift a day depending on the viewer's
// timezone. This formats a date, never a money figure — that rule only
// covers numbers, not calendar text.
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

function showToast(message, kind = "default") {
  const stack = document.getElementById("toast-stack");
  if (!stack) return;
  const toast = document.createElement("div");
  toast.className = kind === "danger" ? "toast toast-danger" : "toast";
  toast.textContent = message;
  stack.appendChild(toast);

  requestAnimationFrame(() => toast.classList.add("is-visible"));
  setTimeout(() => {
    toast.classList.remove("is-visible");
    setTimeout(() => toast.remove(), 220);
  }, 3400);
}

// Popovers open across re-renders of this page; track the live set so a
// click anywhere else on the page closes whichever one is open. Reset on
// every render() call since the DOM backing older entries is replaced.
let openPopovers = [];
function closeAllPopovers() {
  openPopovers.forEach((popover) => {
    popover.hidden = true;
  });
}
// Registered once at module load (ES modules are singletons — this file is
// only evaluated once no matter how many times render() runs).
document.addEventListener("click", closeAllPopovers);

export async function render(root) {
  renderLoading(root);

  let accounts;
  try {
    accounts = await api.listAccounts();
  } catch (err) {
    renderError(root, err, () => render(root));
    return;
  }

  if (!accounts.length) {
    renderEmpty(root);
    return;
  }

  renderAccounts(root, accounts);
}

function renderLoading(root) {
  root.innerHTML = `
    <div class="accounts-page">
      <header class="accounts-intro">
        <h1 class="accounts-intro-title">Accounts</h1>
        <p class="accounts-intro-lede">Every account you've connected, in one calm place.</p>
      </header>
      <div class="accounts-grid">
        ${[0, 1, 2].map(() => skeletonCardMarkup()).join("")}
      </div>
    </div>
  `;
}

function skeletonCardMarkup() {
  return `
    <div class="card account-card">
      <div class="account-card-head">
        <div class="skeleton skeleton-text" style="width: 40%; height: 0.8em;"></div>
        <div class="skeleton skeleton-text" style="width: 65%; height: 1.1em; margin-top: 10px;"></div>
      </div>
      <div class="account-card-bottom">
        <div class="skeleton" style="width: 60%; height: 2.3em;"></div>
        <div class="skeleton skeleton-text" style="width: 30%; height: 0.7em; margin-top: 8px;"></div>
      </div>
    </div>
  `;
}

function renderError(root, err, retry) {
  root.innerHTML = `
    <div class="empty-state">
      <p class="empty-state-title">Couldn't load your accounts</p>
      <p class="empty-state-body"></p>
      <button class="btn btn-ghost accounts-state-action" type="button" id="accounts-retry">Try again</button>
    </div>
  `;
  root.querySelector(".empty-state-body").textContent =
    (err && err.detail) || "Something went wrong reaching BlitZave.";
  root.querySelector("#accounts-retry").addEventListener("click", retry);
}

function renderEmpty(root) {
  root.innerHTML = `
    <div class="empty-state">
      <p class="empty-state-title">Nothing connected yet</p>
      <p class="empty-state-body">Sync with your bank aggregator to bring your accounts in.</p>
      <button class="btn btn-primary accounts-state-action" type="button" id="accounts-sync">Sync accounts</button>
    </div>
  `;

  const btn = root.querySelector("#accounts-sync");
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    btn.textContent = "Syncing…";
    try {
      await api.sync();
      localStorage.setItem("bz.lastSyncedAt", new Date().toISOString());
      await render(root);
    } catch (err) {
      showToast(
        (err && err.detail) || "Couldn't sync right now. Try again in a moment.",
        "danger"
      );
      btn.disabled = false;
      btn.textContent = "Sync accounts";
    }
  });
}

function renderAccounts(root, accounts) {
  openPopovers = [];

  const assets = accounts.filter((a) => a.account_type === "asset");
  const liabilities = accounts.filter((a) => a.account_type === "liability");

  root.innerHTML = `
    <div class="accounts-page">
      <header class="accounts-intro">
        <h1 class="accounts-intro-title">Accounts</h1>
        <p class="accounts-intro-lede">Every account you've connected, in one calm place.</p>
      </header>
      <div id="accounts-groups"></div>
    </div>
  `;

  const groupsEl = root.querySelector("#accounts-groups");
  if (assets.length) groupsEl.appendChild(renderGroup(root, "Assets", assets));
  if (liabilities.length) groupsEl.appendChild(renderGroup(root, "Cards", liabilities));
}

function renderGroup(root, title, accounts) {
  const section = document.createElement("section");
  section.className = "accounts-group";

  const heading = document.createElement("h2");
  heading.className = "accounts-group-title";
  heading.textContent = title;
  section.appendChild(heading);

  const grid = document.createElement("div");
  grid.className = "accounts-grid";
  accounts.forEach((account) => grid.appendChild(renderCard(root, account)));
  section.appendChild(grid);

  return section;
}

function renderCard(root, account) {
  const isWallet = account.account_type === "liability";

  const card = document.createElement("article");
  card.className = isWallet
    ? "card card-interactive account-card account-card--wallet"
    : "card card-interactive account-card";
  card.tabIndex = 0;
  card.setAttribute("role", "button");
  card.setAttribute("aria-label", `Open ${account.name || account.display_name}`);

  const head = document.createElement("div");
  head.className = "account-card-head";

  const top = document.createElement("div");
  top.className = "account-card-top";

  const menuWrap = document.createElement("div");
  menuWrap.className = "account-card-menu";

  const menuBtn = document.createElement("button");
  menuBtn.type = "button";
  menuBtn.className = "account-card-menu-btn";
  menuBtn.setAttribute("aria-label", "Account options");
  menuBtn.innerHTML = ICON_ELLIPSIS;

  const popover = buildEditPopover(root, account);
  openPopovers.push(popover);

  menuBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    const wasHidden = popover.hidden;
    closeAllPopovers();
    popover.hidden = !wasHidden;
  });

  menuWrap.appendChild(menuBtn);
  menuWrap.appendChild(popover);

  const name = document.createElement("p");
  name.className = "account-card-name";
  name.textContent = account.name || account.display_name;

  if (isWallet) {
    // Wallet face: issuer small at top-left, ellipsis at top-right, card
    // name below. No badge — the issuer line already tells you what this
    // is; a redundant "Credit card" pill would just be noise on a face
    // that's already doing one thing.
    const issuer = document.createElement("span");
    issuer.className = "account-card-issuer";
    issuer.textContent = institution(account);

    top.appendChild(issuer);
    top.appendChild(menuWrap);

    head.appendChild(top);
    head.appendChild(name);
  } else {
    // Asset face: name + institution on the left, type badge + menu on
    // the right — the original identity block, just with the bolder
    // balance treatment below.
    const idBlock = document.createElement("div");
    idBlock.appendChild(name);
    const instLabel = institution(account);
    if (instLabel) {
      const inst = document.createElement("p");
      inst.className = "account-card-institution";
      inst.textContent = instLabel;
      idBlock.appendChild(inst);
    }

    const actions = document.createElement("div");
    actions.className = "account-card-actions";
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = subtypeLabel(account);
    actions.appendChild(badge);
    actions.appendChild(menuWrap);

    top.appendChild(idBlock);
    top.appendChild(actions);

    head.appendChild(top);
  }

  const bottom = document.createElement("div");
  bottom.className = "account-card-bottom";

  const balance = document.createElement("p");
  balance.className = "account-card-balance u-tabular";
  balance.textContent = account.balance_display;

  const asOf = document.createElement("p");
  asOf.className = "account-card-asof";
  asOf.textContent = account.balance_as_of ? `as of ${formatAsOf(account.balance_as_of)}` : "";

  bottom.appendChild(balance);
  bottom.appendChild(asOf);

  card.appendChild(head);
  card.appendChild(bottom);

  card.addEventListener("click", () => navigate(`/accounts/${account.id}`));
  card.addEventListener("keydown", (e) => {
    // Only the card's own focus should trigger navigation — not Enter/Space
    // bubbling up from the menu button or popover controls inside it.
    if (e.target !== card) return;
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      navigate(`/accounts/${account.id}`);
    }
  });

  return card;
}

function buildEditPopover(root, account) {
  const popover = document.createElement("div");
  popover.className = "account-edit-popover";
  popover.hidden = true;
  popover.addEventListener("click", (e) => e.stopPropagation());

  const typeField = document.createElement("div");
  typeField.className = "field";
  const typeLabel = document.createElement("span");
  typeLabel.className = "field-label";
  typeLabel.textContent = "Account type";
  const typeSelect = document.createElement("select");
  typeSelect.className = "select";
  typeSelect.setAttribute("aria-label", "Account type");
  [
    ["asset", "Asset"],
    ["liability", "Liability"],
  ].forEach(([value, label]) => {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = label;
    typeSelect.appendChild(opt);
  });
  typeSelect.value = account.account_type;
  typeField.appendChild(typeLabel);
  typeField.appendChild(typeSelect);

  const subtypeField = document.createElement("div");
  subtypeField.className = "field";
  const subtypeFieldLabel = document.createElement("span");
  subtypeFieldLabel.className = "field-label";
  subtypeFieldLabel.textContent = "Subtype";
  const subtypeSelect = document.createElement("select");
  subtypeSelect.className = "select";
  subtypeSelect.setAttribute("aria-label", "Account subtype");
  subtypeField.appendChild(subtypeFieldLabel);
  subtypeField.appendChild(subtypeSelect);

  function populateSubtype(type, preselect) {
    subtypeSelect.innerHTML = "";
    const options = type === "asset" ? ASSET_LABELS : LIABILITY_LABELS;
    Object.entries(options).forEach(([value, label]) => {
      const opt = document.createElement("option");
      opt.value = value;
      opt.textContent = label;
      subtypeSelect.appendChild(opt);
    });
    if (preselect && options[preselect]) subtypeSelect.value = preselect;
  }

  populateSubtype(
    account.account_type,
    account.account_type === "asset" ? account.asset_type : account.liability_type
  );

  typeSelect.addEventListener("change", () => populateSubtype(typeSelect.value));

  const actionsRow = document.createElement("div");
  actionsRow.className = "account-edit-actions";

  const cancelBtn = document.createElement("button");
  cancelBtn.type = "button";
  cancelBtn.className = "btn btn-quiet";
  cancelBtn.textContent = "Cancel";
  cancelBtn.addEventListener("click", () => {
    popover.hidden = true;
  });

  const saveBtn = document.createElement("button");
  saveBtn.type = "button";
  saveBtn.className = "btn btn-primary";
  saveBtn.textContent = "Save";
  saveBtn.addEventListener("click", async () => {
    saveBtn.disabled = true;
    saveBtn.textContent = "Saving…";

    const body = { account_type: typeSelect.value };
    if (typeSelect.value === "asset") {
      body.asset_type = subtypeSelect.value;
    } else {
      body.liability_type = subtypeSelect.value;
    }

    try {
      await api.patchAccount(account.id, body);
      popover.hidden = true;
      await render(root);
    } catch (err) {
      const message =
        err && err.status === 409
          ? "That account is already set that way."
          : (err && err.detail) || "Couldn't save that change. Try again.";
      showToast(message, "danger");
      saveBtn.disabled = false;
      saveBtn.textContent = "Save";
    }
  });

  actionsRow.appendChild(cancelBtn);
  actionsRow.appendChild(saveBtn);

  popover.appendChild(typeField);
  popover.appendChild(subtypeField);
  popover.appendChild(actionsRow);

  return popover;
}
