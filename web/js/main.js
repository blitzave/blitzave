// BlitZave — app shell.
//
// Builds the sidebar + content frame, decides whether to show onboarding
// or the real app, wires the sync button, and hands the content element to
// the router. Page modules never see any of this — they only get the
// element the router renders them into.

import * as api from "./api.js";
import { startRouter, navigate } from "./router.js";

// Small SF-Symbols-energy line icons, 24px viewBox, currentColor stroke —
// sized and colored by .sidebar-link-icon in base.css.
const ICON_ACCOUNTS = `
  <svg class="sidebar-link-icon" viewBox="0 0 24 24" aria-hidden="true">
    <rect x="2.75" y="6" width="18.5" height="13" rx="2.5" />
    <path d="M2.75 10h18.5" />
    <circle cx="17" cy="14.5" r="1" fill="currentColor" stroke="none" />
  </svg>
`;

const ICON_TRANSACTIONS = `
  <svg class="sidebar-link-icon" viewBox="0 0 24 24" aria-hidden="true">
    <path d="M7 4.5v12.5M7 17l-3-3M7 17l3-3" />
    <path d="M17 19.5V7M17 7l-3 3M17 7l3 3" />
  </svg>
`;

const ICON_CATEGORIES = `
  <svg class="sidebar-link-icon" viewBox="0 0 24 24" aria-hidden="true">
    <rect x="3.25" y="3.25" width="7.5" height="7.5" rx="1.8" />
    <rect x="13.25" y="3.25" width="7.5" height="7.5" rx="1.8" />
    <rect x="3.25" y="13.25" width="7.5" height="7.5" rx="1.8" />
    <rect x="13.25" y="13.25" width="7.5" height="7.5" rx="1.8" />
  </svg>
`;

// A calm circular-refresh glyph — always visible, rotates while syncing
// (see .sidebar-sync-btn.is-syncing .sync-icon in base.css).
const ICON_SYNC = `
  <svg class="sync-icon" viewBox="0 0 24 24" aria-hidden="true">
    <path d="M20 11a8 8 0 0 0-13.7-4.7L4 8.6" />
    <path d="M4 4v4.6h4.6" />
    <path d="M4 13a8 8 0 0 0 13.7 4.7L20 15.4" />
    <path d="M20 20v-4.6h-4.6" />
  </svg>
`;

const NAV_ITEMS = [
  { label: "Accounts", path: "/accounts", icon: ICON_ACCOUNTS },
  { label: "Transactions", path: "/transactions", icon: ICON_TRANSACTIONS },
  { label: "Categories", path: "/categories", icon: ICON_CATEGORIES },
];

const BRAND_MARK = `
  <svg class="sidebar-brand-mark" viewBox="0 0 64 64" aria-hidden="true">
    <rect width="64" height="64" rx="14" fill="var(--accent)" />
    <path d="M14 40 C22 40 26 28 34 28 C42 28 44 18 50 14" stroke="var(--accent-ink)"
      stroke-width="5" stroke-linecap="round" fill="none" />
    <circle cx="50" cy="14" r="4" fill="var(--accent-ink)" />
  </svg>
`;

let shellEl = null;
let contentEl = null;
let syncBtn = null;
let syncHintEl = null;
let toastStackEl = null;

function shellMarkup() {
  return `
    <div class="app-shell" id="app-shell">
      <aside class="sidebar">
        <div class="sidebar-top">
          <div class="sidebar-brand">
            ${BRAND_MARK}
            <span>BlitZave</span>
          </div>
          <nav class="sidebar-nav" aria-label="Main">
            ${NAV_ITEMS.map(
              (item) =>
                `<a class="sidebar-link" data-nav-link href="#${item.path}">${item.icon}<span>${item.label}</span></a>`
            ).join("")}
          </nav>
        </div>
        <div class="sidebar-footer">
          <button type="button" class="btn btn-ghost sidebar-sync-btn" id="sync-btn">
            ${ICON_SYNC}
            <span>Sync now</span>
          </button>
          <p class="sidebar-sync-hint" id="sync-hint"></p>
        </div>
      </aside>
      <main class="content" id="app-content"></main>
    </div>
    <div class="toast-stack" id="toast-stack" aria-live="polite"></div>
  `;
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

  syncBtn.addEventListener("click", handleSync);
  refreshSyncHint();
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
