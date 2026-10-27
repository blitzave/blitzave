// BlitZave — app shell.
//
// Builds the sidebar + content frame, decides whether to show onboarding
// or the real app, wires the sync button, and hands the content element to
// the router. Page modules never see any of this — they only get the
// element the router renders them into.

import * as api from "./api.js";
import { startRouter, navigate } from "./router.js";

// Line icons in the Lucide idiom (24px viewBox, currentColor stroke, round
// caps) — sized and colored by .sidebar-link-icon in base.css.
const ICON_ACCOUNTS = `
  <svg class="sidebar-link-icon" viewBox="0 0 24 24" aria-hidden="true">
    <path d="M3 22h18" />
    <path d="M6 18v-7M10 18v-7M14 18v-7M18 18v-7" />
    <path d="M12 2l8 5H4z" />
  </svg>
`;

const ICON_TRANSACTIONS = `
  <svg class="sidebar-link-icon" viewBox="0 0 24 24" aria-hidden="true">
    <path d="M4 2v20l2-1 2 1 2-1 2 1 2-1 2 1 2-1 2 1V2l-2 1-2-1-2 1-2-1-2 1-2-1-2 1z" />
    <path d="M16 8h-6a2 2 0 1 0 0 4h4a2 2 0 1 1 0 4H8" />
    <path d="M12 17.5v-11" />
  </svg>
`;

const ICON_CATEGORIES = `
  <svg class="sidebar-link-icon" viewBox="0 0 24 24" aria-hidden="true">
    <rect x="3" y="3" width="18" height="18" rx="2" />
    <path d="M3 9a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2" />
    <path d="M3 11h3c.8 0 1.6.3 2.1.9l1.1.9c1.6 1.6 4.1 1.6 5.7 0l1.1-.9c.5-.5 1.3-.9 2.1-.9H21" />
  </svg>
`;

// Circular refresh — always visible, rotates while syncing
// (see .topbar-sync-btn.is-syncing .sync-icon in base.css).
const ICON_SYNC = `
  <svg class="sync-icon" viewBox="0 0 24 24" aria-hidden="true">
    <path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" />
    <path d="M21 3v5h-5" />
    <path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16" />
    <path d="M8 16H3v5" />
  </svg>
`;

const ICON_LOCK = `
  <svg class="sidebar-footer-icon" viewBox="0 0 24 24" aria-hidden="true">
    <rect x="3" y="11" width="18" height="11" rx="2" />
    <path d="M7 11V7a5 5 0 0 1 10 0v4" />
  </svg>
`;

const NAV_ITEMS = [
  { label: "Accounts", path: "/accounts", icon: ICON_ACCOUNTS },
  { label: "Transactions", path: "/transactions", icon: ICON_TRANSACTIONS },
  { label: "Categories", path: "/categories", icon: ICON_CATEGORIES },
];

// What the sticky top bar says for each section. Matched by prefix, so
// #/accounts/ACT-1 falls through to the "/accounts/" entry first.
const TOPBAR_COPY = [
  { prefix: "/accounts/", title: "Account", subtitle: "One account and everything that moved through it." },
  { prefix: "/accounts", title: "Accounts", subtitle: "Balances across everything you've connected." },
  { prefix: "/transactions", title: "Transactions", subtitle: "Review and categorize your recent activity." },
  { prefix: "/categories", title: "Categories", subtitle: "Monthly budgets, with room to adjust." },
  { prefix: "/onboarding", title: "Setup", subtitle: "One token to paste, then you're done." },
];

const BRAND_MARK = `
  <span class="sidebar-brand-mark" aria-hidden="true">
    <svg viewBox="0 0 24 24">
      <path d="M9.94 15.5A2 2 0 0 0 8.5 14.06l-6.14-1.58a.5.5 0 0 1 0-.96L8.5 9.94A2 2 0 0 0 9.94 8.5l1.58-6.14a.5.5 0 0 1 .96 0l1.58 6.14a2 2 0 0 0 1.44 1.44l6.14 1.58a.5.5 0 0 1 0 .96l-6.14 1.58a2 2 0 0 0-1.44 1.44l-1.58 6.14a.5.5 0 0 1-.96 0z" />
      <path d="M20 3v4M22 5h-4" />
      <circle cx="4" cy="20" r="1.5" />
    </svg>
  </span>
`;

let shellEl = null;
let contentEl = null;
let syncBtn = null;
let syncHintEl = null;
let toastStackEl = null;
let topbarTitleEl = null;
let topbarSubtitleEl = null;

function shellMarkup() {
  return `
    <div class="app-glow" aria-hidden="true">
      <span class="app-glow-blue"></span>
      <span class="app-glow-teal"></span>
    </div>
    <div class="app-shell" id="app-shell">
      <aside class="sidebar">
        <div class="sidebar-top">
          <a class="sidebar-brand" href="#/accounts">
            ${BRAND_MARK}
            <span class="sidebar-brand-text">
              <span class="sidebar-brand-name">BlitZave</span>
              <span class="sidebar-brand-tag">Money clarity OS</span>
            </span>
          </a>
          <nav class="sidebar-nav" aria-label="Main">
            ${NAV_ITEMS.map(
              (item) =>
                `<a class="sidebar-link" data-nav-link href="#${item.path}">${item.icon}<span>${item.label}</span></a>`
            ).join("")}
          </nav>
        </div>
        <div class="sidebar-footer">
          <p class="sidebar-footer-note">${ICON_LOCK}<span>Runs on this laptop. Bank access is read-only.</span></p>
          <p class="sidebar-footer-meta">Open source · self-hosted</p>
        </div>
      </aside>
      <div class="main-col">
        <header class="topbar">
          <div class="topbar-text">
            <p class="topbar-title" id="topbar-title"></p>
            <p class="topbar-subtitle" id="topbar-subtitle"></p>
          </div>
          <div class="topbar-actions">
            <span class="topbar-sync-hint" id="sync-hint"></span>
            <button type="button" class="btn btn-ghost topbar-sync-btn" id="sync-btn">
              ${ICON_SYNC}
              <span>Sync now</span>
            </button>
          </div>
        </header>
        <main class="content" id="app-content"></main>
      </div>
    </div>
    <div class="toast-stack" id="toast-stack" aria-live="polite"></div>
  `;
}

function refreshTopbar() {
  if (!topbarTitleEl) return;
  const path = window.location.hash.slice(1).split("?")[0] || "/accounts";
  const copy = TOPBAR_COPY.find((c) => path.startsWith(c.prefix)) || TOPBAR_COPY[1];
  topbarTitleEl.textContent = copy.title;
  topbarSubtitleEl.textContent = copy.subtitle;
}

function relativeSyncLabel() {
  const iso = localStorage.getItem("bz.lastSyncedAt");
  if (!iso) return "Not synced yet";

  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return "Not synced yet";

  const minutes = Math.round((Date.now() - then.getTime()) / 60000);
  if (minutes < 1) return "Synced just now";
  if (minutes < 60) return `Synced ${minutes}m ago`;

  const hours = Math.round(minutes / 60);
  if (hours < 24) return `Synced ${hours}h ago`;

  const days = Math.round(hours / 24);
  if (days === 1) return "Synced yesterday";
  return `Synced ${days}d ago`;
}

function refreshSyncHint() {
  if (syncHintEl) syncHintEl.textContent = relativeSyncLabel();
}

function showToast(message, kind = "default") {
  if (!toastStackEl) return;
  const toast = document.createElement("div");
  toast.className = kind === "danger" ? "toast toast-danger" : "toast";
  toast.textContent = message;
  toastStackEl.appendChild(toast);

  requestAnimationFrame(() => toast.classList.add("is-visible"));
  setTimeout(() => {
    toast.classList.remove("is-visible");
    setTimeout(() => toast.remove(), 220);
  }, 3400);
}

async function handleSync() {
  if (!syncBtn || syncBtn.disabled) return;

  syncBtn.disabled = true;
  syncBtn.classList.add("is-syncing");
  try {
    await api.sync();
    localStorage.setItem("bz.lastSyncedAt", new Date().toISOString());
    showToast("Synced — everything's up to date.");
  } catch (err) {
    showToast(err.detail || "Couldn't sync right now. Try again in a moment.", "danger");
  } finally {
    syncBtn.disabled = false;
    syncBtn.classList.remove("is-syncing");
    refreshSyncHint();
  }
}

function wireShell() {
  shellEl = document.getElementById("app-shell");
  contentEl = document.getElementById("app-content");
  syncBtn = document.getElementById("sync-btn");
  syncHintEl = document.getElementById("sync-hint");
  toastStackEl = document.getElementById("toast-stack");
  topbarTitleEl = document.getElementById("topbar-title");
  topbarSubtitleEl = document.getElementById("topbar-subtitle");

  syncBtn.addEventListener("click", handleSync);
  window.addEventListener("hashchange", refreshTopbar);
  refreshSyncHint();
  refreshTopbar();
}

/** Called by onboarding.js once setup is complete and it has already set
 * localStorage['bz.onboarded'] = '1'. Reveals the sidebar and sends the
 * user into the real app. */
function refreshShell() {
  if (shellEl) shellEl.classList.remove("is-onboarding");
  refreshSyncHint();
  navigate("/accounts");
}

async function boot() {
  document.getElementById("app").innerHTML = shellMarkup();
  wireShell();

  let connected = false;
  try {
    const status = await api.connectStatus();
    connected = Boolean(status && status.connected);
  } catch {
    connected = false; // backend unreachable reads the same as "not connected yet"
  }

  const onboarded = localStorage.getItem("bz.onboarded") === "1";

  if (!connected || !onboarded) {
    shellEl.classList.add("is-onboarding");
    navigate("/onboarding");
  }

  startRouter(contentEl);
}

window.__bz = { refreshShell };

boot();
