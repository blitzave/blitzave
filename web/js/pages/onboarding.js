// BlitZave — onboarding. The user's first impression: one idea per step,
// a slim progress indicator, 200ms step transitions. Nothing here computes
// money — balances only ever pass through *_display strings from the API,
// and the only numbers this page invents itself are plain counts (accounts
// synced, transactions synced), never money.
//
// Handoff contract (see DESIGN.md): once onboarding is done —
//   localStorage.setItem('bz.onboarded', '1');
//   window.__bz.refreshShell();
// refreshShell() reveals the sidebar and navigates to #/accounts itself.

import * as api from "../api.js";

const TOTAL_STEPS = 5;
const STEP_TRANSITION_MS = 200; // matches --t-base

const BRAND_MARK = `
  <svg class="onboarding-brand-mark" viewBox="0 0 64 64" aria-hidden="true">
    <rect width="64" height="64" rx="14" fill="var(--bg-well)" />
    <path d="M14 40 C22 40 26 28 34 28 C42 28 44 18 50 14" stroke="var(--accent)"
      stroke-width="5" stroke-linecap="round" fill="none" />
    <circle cx="50" cy="14" r="4" fill="var(--accent)" />
  </svg>
`;

function pluralize(n, word) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

function finishOnboarding() {
  localStorage.setItem("bz.onboarded", "1");
  window.__bz.refreshShell();
}

function skipLinkMarkup() {
  return `<button type="button" class="btn btn-quiet onboarding-skip" data-action="skip">Skip for now</button>`;
}

function focusHeading(panel) {
  const heading = panel.querySelector("[data-step-focus]");
  if (heading) heading.focus();
}

export function render(root, _params) {
  root.innerHTML = `
    <div class="onboarding">
      <div class="onboarding-progress" aria-hidden="true">
        ${Array.from(
          { length: TOTAL_STEPS },
          (_, i) => `<span class="onboarding-progress-seg" data-seg="${i + 1}"></span>`
        ).join("")}
      </div>
      <div class="card onboarding-panel" id="onboarding-panel"></div>
    </div>
  `;

  const panel = root.querySelector("#onboarding-panel");
  const progressSegs = Array.from(root.querySelectorAll(".onboarding-progress-seg"));

  function updateProgress(step) {
    progressSegs.forEach((seg, i) => {
      seg.classList.toggle("is-complete", i + 1 < step);
      seg.classList.toggle("is-active", i + 1 === step);
    });
  }

  const ctx = { goTo, skip };

  function skip() {
    finishOnboarding();
  }

  function goTo(step) {
    const renderFn = STEP_RENDERERS[step];
    if (!renderFn) return;
    updateProgress(step);

    const isFirstPaint = panel.childElementCount === 0;
    if (isFirstPaint) {
      renderFn(panel, ctx);
      requestAnimationFrame(() => {
        panel.classList.add("is-visible");
        focusHeading(panel);
      });
      return;
    }

    panel.classList.remove("is-visible");
    window.setTimeout(() => {
      if (!panel.isConnected) return; // navigated away mid-transition
      renderFn(panel, ctx);
      requestAnimationFrame(() => {
        panel.classList.add("is-visible");
        focusHeading(panel);
      });
    }, STEP_TRANSITION_MS);
  }

  const STEP_RENDERERS = {
    1: renderWelcome,
    2: renderConnect,
    3: renderSync,
    4: renderAccounts,
    5: renderFinish,
  };

  goTo(1);
}

// -------------------------------- Step 1 --------------------------------
// Welcome. Brand moment only — no feature laundry list.

function renderWelcome(panel, ctx) {
  panel.innerHTML = `
    <div class="onboarding-step onboarding-step-center">
      <div class="onboarding-brand">
        ${BRAND_MARK}
        <span class="onboarding-brand-name" tabindex="-1" data-step-focus>BlitZave</span>
      </div>
      <p class="onboarding-tagline">Clarity over money — through confidence, not data overload.</p>
      <div class="onboarding-actions onboarding-actions-center">
        <button type="button" class="btn btn-primary onboarding-cta" data-action="next">Get started</button>
      </div>
    </div>
  `;
  panel.querySelector('[data-action="next"]').addEventListener("click", () => ctx.goTo(2));
}

// -------------------------------- Step 2 --------------------------------
// Connect. Checks connectStatus() fresh on every entry — never assumes.

function renderConnect(panel, ctx) {
  panel.innerHTML = `
    <div class="onboarding-step">
      <p class="onboarding-eyebrow">Step 2 of 5</p>
      <h2 class="onboarding-title" tabindex="-1" data-step-focus>Connect your bank</h2>
      <div id="connect-body" class="onboarding-dynamic">
        <div class="onboarding-check">
          <span class="onboarding-spinner-ring" aria-hidden="true"></span>
          <span>Checking connection…</span>
        </div>
      </div>
      ${skipLinkMarkup()}
    </div>
  `;

  wireSkip(panel, ctx);

  const body = panel.querySelector("#connect-body");

  api
    .connectStatus()
    .then((status) => {
      if (!panel.isConnected) return;
      if (status && status.connected) {
        renderAlreadyConnected(body, ctx);
      } else {
        renderConnectForm(body, ctx);
      }
    })
    .catch(() => {
      if (!panel.isConnected) return;
      // Backend unreachable reads the same as "not connected yet" — let
      // them try the form rather than stall on a check that can't answer.
      renderConnectForm(body, ctx);
    });
}

function renderAlreadyConnected(body, ctx) {
  body.innerHTML = `
    <p class="onboarding-body">Already connected — your bank link is ready to go.</p>
    <div class="onboarding-actions">
      <button type="button" class="btn btn-primary onboarding-cta" data-action="continue">Continue</button>
    </div>
  `;
  body.querySelector('[data-action="continue"]').addEventListener("click", () => ctx.goTo(3));
}

function renderConnectForm(body, ctx) {
  body.innerHTML = `
    <p class="onboarding-body">SimpleFIN gives BlitZave read-only access to your accounts — it can see
      balances and transactions, but it can never move money. The setup token stays in your Mac's
      keychain, and nothing leaves this machine except the sync itself.</p>
    <form id="connect-form" class="onboarding-form" novalidate>
      <div class="field">
        <label class="field-label" for="setup-token">Setup token</label>
        <textarea class="input onboarding-token-input" id="setup-token" rows="3"
          placeholder="Paste the token from SimpleFIN"></textarea>
        <p class="field-error" id="connect-error" hidden></p>
      </div>
      <div class="onboarding-actions">
        <button type="submit" class="btn btn-primary onboarding-cta" id="connect-submit">Connect</button>
      </div>
    </form>
  `;

  const form = body.querySelector("#connect-form");
  const textarea = body.querySelector("#setup-token");
  const errorEl = body.querySelector("#connect-error");
  const submitBtn = body.querySelector("#connect-submit");

  function showError(message) {
    errorEl.textContent = message;
    errorEl.hidden = false;
  }

  function clearError() {
    errorEl.hidden = true;
    errorEl.textContent = "";
  }

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const token = textarea.value.trim();
    if (!token) {
      showError("Paste your setup token to continue.");
      textarea.focus();
      return;
    }

    clearError();
    submitBtn.disabled = true;
    submitBtn.textContent = "Connecting…";

    try {
      await api.connect(token);
      ctx.goTo(3);
    } catch (err) {
      if (!body.isConnected) return;
      showError(err.detail || "Couldn't connect. Double-check the token and try again.");
      submitBtn.disabled = false;
      submitBtn.textContent = "Connect";
    }
  });
}

// -------------------------------- Step 3 --------------------------------
// First sync. Auto-starts on entry with a calm breathing indicator.

function renderSync(panel, ctx) {
  panel.innerHTML = `
    <div class="onboarding-step">
      <p class="onboarding-eyebrow">Step 3 of 5</p>
      <h2 class="onboarding-title" tabindex="-1" data-step-focus>Bringing in your accounts</h2>
      <div id="sync-body" class="onboarding-dynamic"></div>
      ${skipLinkMarkup()}
    </div>
  `;

  wireSkip(panel, ctx);

  const body = panel.querySelector("#sync-body");
  runSync(body, ctx);
}

function runSync(body, ctx) {
  body.innerHTML = `
    <div class="onboarding-sync-indicator" aria-live="polite">
      <span class="onboarding-sync-dot" aria-hidden="true"></span>
      <p class="onboarding-body">Syncing your accounts…</p>
    </div>
  `;

  api
    .sync()
    .then((result) => {
      if (!body.isConnected) return;
      localStorage.setItem("bz.lastSyncedAt", new Date().toISOString());
      const accountCount = Number.isFinite(result?.accounts) ? result.accounts : 0;
      const txCount = Number.isFinite(result?.transactions) ? result.transactions : 0;
      body.innerHTML = `
        <p class="onboarding-body">${pluralize(accountCount, "account")},
          ${pluralize(txCount, "transaction")} — you're all set.</p>
        <div class="onboarding-actions">
          <button type="button" class="btn btn-primary onboarding-cta" data-action="continue">Continue</button>
        </div>
      `;
      body.querySelector('[data-action="continue"]').addEventListener("click", () => ctx.goTo(4));
    })
    .catch((err) => {
      if (!body.isConnected) return;
      body.innerHTML = `
        <p class="onboarding-body">${err.detail || "The sync didn't go through."} No rush — try again
          when you're ready.</p>
        <div class="onboarding-actions">
          <button type="button" class="btn btn-ghost" data-action="retry">Try again</button>
        </div>
      `;
      body.querySelector('[data-action="retry"]').addEventListener("click", () => runSync(body, ctx));
    });
}

// -------------------------------- Step 4 --------------------------------
// Meet your accounts.

function accountSubtitle(acc) {
  if (acc.account_type === "liability") return acc.issuer_name || acc.bank_name || "";
  return acc.bank_name || acc.issuer_name || "";
}

function renderAccounts(panel, ctx) {
  panel.innerHTML = `
    <div class="onboarding-step">
      <p class="onboarding-eyebrow">Step 4 of 5</p>
      <h2 class="onboarding-title" tabindex="-1" data-step-focus>Meet your accounts</h2>
      <div id="accounts-body" class="onboarding-dynamic">
        <div class="onboarding-accounts">
          <div class="skeleton" style="height: 56px;"></div>
          <div class="skeleton" style="height: 56px;"></div>
          <div class="skeleton" style="height: 56px;"></div>
        </div>
      </div>
      ${skipLinkMarkup()}
    </div>
  `;

  wireSkip(panel, ctx);

  const body = panel.querySelector("#accounts-body");
  loadAccounts(body, ctx);
}

function loadAccounts(body, ctx) {
  api
    .listAccounts()
    .then((accounts) => {
      if (!body.isConnected) return;

      if (!accounts || accounts.length === 0) {
        body.innerHTML = `
          <p class="onboarding-body">Nothing synced yet — that's alright, accounts will show up here
            once a sync brings them in.</p>
          <div class="onboarding-actions">
            <button type="button" class="btn btn-primary onboarding-cta" data-action="continue">Continue</button>
          </div>
        `;
        body.querySelector('[data-action="continue"]').addEventListener("click", () => ctx.goTo(5));
        return;
      }

      const cards = accounts
        .map(
          (acc) => `
          <div class="onboarding-account-card">
            <div>
              <p class="onboarding-account-name">${escapeHtml(acc.display_name)}</p>
              <p class="onboarding-account-sub">${escapeHtml(accountSubtitle(acc))}</p>
            </div>
            <p class="onboarding-account-balance u-tabular">${escapeHtml(acc.balance_display)}</p>
          </div>`
        )
        .join("");

      body.innerHTML = `
        <div class="onboarding-accounts">${cards}</div>
        <p class="onboarding-hint">We guessed each account's type — you can change any of them any time.</p>
        <div class="onboarding-actions">
          <button type="button" class="btn btn-primary onboarding-cta" data-action="continue">Continue</button>
        </div>
      `;
      body.querySelector('[data-action="continue"]').addEventListener("click", () => ctx.goTo(5));
    })
    .catch((err) => {
      if (!body.isConnected) return;
      body.innerHTML = `
        <p class="onboarding-body">${err.detail || "Couldn't load your accounts just now."}</p>
        <div class="onboarding-actions">
          <button type="button" class="btn btn-ghost" data-action="retry">Try again</button>
        </div>
      `;
      body.querySelector('[data-action="retry"]').addEventListener("click", () => loadAccounts(body, ctx));
    });
}

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = value == null ? "" : String(value);
  return div.innerHTML;
}

// -------------------------------- Step 5 --------------------------------
// Three pointers, then into the app.

function renderFinish(panel, _ctx) {
  panel.innerHTML = `
    <div class="onboarding-step">
      <p class="onboarding-eyebrow">Step 5 of 5</p>
      <h2 class="onboarding-title" tabindex="-1" data-step-focus>You're set</h2>
      <div class="onboarding-pointers">
        <div class="onboarding-pointer">
          <span class="onboarding-pointer-dot" aria-hidden="true"></span>
          <div>
            <p class="onboarding-pointer-title">Transactions</p>
            <p class="onboarding-pointer-body">Review what came in and categorize what matters.</p>
          </div>
        </div>
        <div class="onboarding-pointer">
          <span class="onboarding-pointer-dot" aria-hidden="true"></span>
          <div>
            <p class="onboarding-pointer-title">Categories</p>
            <p class="onboarding-pointer-body">See where it's going, organized your way.</p>
          </div>
        </div>
        <div class="onboarding-pointer">
          <span class="onboarding-pointer-dot" aria-hidden="true"></span>
          <div>
            <p class="onboarding-pointer-title">Sync, anytime</p>
            <p class="onboarding-pointer-body">One click, top of the sidebar, whenever you want the latest.</p>
          </div>
        </div>
      </div>
      <div class="onboarding-actions">
        <button type="button" class="btn btn-primary onboarding-cta" data-action="finish">Take me in</button>
      </div>
    </div>
  `;
  panel.querySelector('[data-action="finish"]').addEventListener("click", finishOnboarding);
}

// ------------------------------ Shared bits ------------------------------

function wireSkip(panel, ctx) {
  const skipBtn = panel.querySelector('[data-action="skip"]');
  if (skipBtn) skipBtn.addEventListener("click", () => ctx.skip());
}
