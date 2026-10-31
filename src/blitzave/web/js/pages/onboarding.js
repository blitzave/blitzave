// BlitZave — onboarding. The user's first impression: one idea per step,
// a slim progress indicator, 240ms step transitions. Nothing here computes
// money — balances only ever pass through *_display strings from the API,
// and the only numbers this page invents itself are plain counts (accounts
// synced, transactions synced), never money.
//
// Handoff contract (see DESIGN.md): once onboarding is done —
//   localStorage.setItem('bz.onboarded', '1');
//   window.__bz.refreshShell();
// refreshShell() reveals the sidebar and navigates to #/accounts itself.

// Versioned import: a tab left open across an upgrade still holds the old
// api.js, which has no setUpdateCheck (see LESSONS.md 13).
import * as api from "../api.js?updates=1";

const TOTAL_STEPS = 5;
const STEP_TRANSITION_MS = 240; // matches onboarding.css's bespoke page-turn duration

const BRAND_MARK = `
  <span class="onboarding-brand-mark" aria-hidden="true">
    <svg viewBox="0 0 24 24">
      <path d="M9.94 15.5A2 2 0 0 0 8.5 14.06l-6.14-1.58a.5.5 0 0 1 0-.96L8.5 9.94A2 2 0 0 0 9.94 8.5l1.58-6.14a.5.5 0 0 1 .96 0l1.58 6.14a2 2 0 0 0 1.44 1.44l6.14 1.58a.5.5 0 0 1 0 .96l-6.14 1.58a2 2 0 0 0-1.44 1.44l-1.58 6.14a.5.5 0 0 1-.96 0z" />
      <path d="M20 3v4M22 5h-4" />
      <circle cx="4" cy="20" r="1.5" />
    </svg>
  </span>
`;

// Small inline icons, scoped to onboarding only. The three "mini" icons
// deliberately mirror the shapes in main.js's sidebar nav (same paths, a
// touch smaller) so the step-5 preview reads as a promise of the real app,
// not a different visual language.
const ICON_LOCK = `
  <span class="onboarding-tile onboarding-tile-round" aria-hidden="true">
    <svg viewBox="0 0 24 24">
      <rect x="5" y="11" width="14" height="9" rx="2.2" />
      <path d="M8 11V8a4 4 0 0 1 8 0v3" />
    </svg>
  </span>
`;

// The only two places this flow uses pine — both are a confirmed action.
const ICON_CHECK = `
  <span class="onboarding-tile onboarding-tile-round onboarding-tile-success" aria-hidden="true">
    <svg viewBox="0 0 24 24">
      <path d="M5.5 12.5l4.5 4.5L18.5 8" />
    </svg>
  </span>
`;

// Account-kind glyphs for the step-4 preview: the same card shape as the
// sidebar's Accounts icon for credit cards, a bank facade for cash accounts.
const ICON_CARD = `
  <svg viewBox="0 0 24 24" aria-hidden="true">
    <rect x="2.75" y="6" width="18.5" height="13" rx="2.5" />
    <path d="M2.75 10h18.5" />
    <circle cx="17" cy="14.5" r="1" fill="currentColor" stroke="none" />
  </svg>
`;

const ICON_BANK = `
  <svg viewBox="0 0 24 24" aria-hidden="true">
    <path d="M3.5 9.5 12 4l8.5 5.5" />
    <path d="M5.5 10v7M10 10v7M14 10v7M18.5 10v7" />
    <path d="M3.5 19.5h17" />
  </svg>
`;

const ASSET_LABELS = { checking: "Checking", savings: "Savings" };
const LIABILITY_LABELS = { credit_card: "Credit card", loan: "Loan" };

const ICON_TRANSACTIONS_MINI = `
  <svg viewBox="0 0 24 24" aria-hidden="true">
    <path d="M7 4.5v12.5M7 17l-3-3M7 17l3-3" />
    <path d="M17 19.5V7M17 7l-3 3M17 7l3 3" />
  </svg>
`;

const ICON_CATEGORIES_MINI = `
  <svg viewBox="0 0 24 24" aria-hidden="true">
    <rect x="3.25" y="3.25" width="7.5" height="7.5" rx="1.8" />
    <rect x="13.25" y="3.25" width="7.5" height="7.5" rx="1.8" />
    <rect x="3.25" y="13.25" width="7.5" height="7.5" rx="1.8" />
    <rect x="13.25" y="13.25" width="7.5" height="7.5" rx="1.8" />
  </svg>
`;

const ICON_SYNC_MINI = `
  <svg viewBox="0 0 24 24" aria-hidden="true">
    <path d="M20 11a8 8 0 0 0-13.7-4.7L4 8.6" />
    <path d="M4 4v4.6h4.6" />
    <path d="M4 13a8 8 0 0 0 13.7 4.7L20 15.4" />
    <path d="M20 20v-4.6h-4.6" />
  </svg>
`;

function pluralize(n, word) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

// Backend error text arrives as a bare lowercase clause ("could not reach
// SimpleFIN: …"). Make it read as a sentence before more copy follows it.
function asSentence(text) {
  const t = String(text || "").trim();
  if (!t) return "";
  const capped = t[0].toUpperCase() + t.slice(1);
  return /[.!?…]$/.test(capped) ? capped : `${capped}.`;
}

function finishOnboarding() {
  localStorage.setItem("bz.onboarded", "1");
  window.__bz.refreshShell();
}

function stepHeadMarkup(step, title) {
  return `
    <header class="onboarding-head">
      <p class="onboarding-eyebrow">Step ${step} of ${TOTAL_STEPS}</p>
      <h2 class="onboarding-title" tabindex="-1" data-step-focus>${title}</h2>
    </header>
  `;
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

    // Step 1 drops the card chrome entirely (see .onboarding-panel.is-bare
    // in onboarding.css) — toggle it in step with the content swap so it
    // never flashes while the panel is visible.
    const swap = () => {
      panel.classList.toggle("is-bare", step === 1);
      renderFn(panel, ctx);
    };

    const isFirstPaint = panel.childElementCount === 0;
    if (isFirstPaint) {
      swap();
      requestAnimationFrame(() => {
        panel.classList.add("is-visible");
        focusHeading(panel);
      });
      return;
    }

    panel.classList.remove("is-visible");
    window.setTimeout(() => {
      if (!panel.isConnected) return; // navigated away mid-transition
      swap();
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
      ${stepHeadMarkup(2, "Connect your bank")}
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
    <div class="onboarding-confirmed">
      ${ICON_CHECK}
      <p class="onboarding-body">Already connected — your bank link is ready to go.</p>
    </div>
    <div class="onboarding-actions">
      <button type="button" class="btn btn-primary onboarding-cta" data-action="continue">Continue</button>
    </div>
  `;
  body.querySelector('[data-action="continue"]').addEventListener("click", () => ctx.goTo(3));
}

function renderConnectForm(body, ctx) {
  body.innerHTML = `
    <div class="onboarding-trust">
      ${ICON_LOCK}
      <p class="onboarding-body onboarding-body-sm">SimpleFIN gives BlitZave read-only access to your accounts — it can see
        balances and transactions, but it can never move money. The setup token stays in your Mac's
        keychain, and nothing leaves this machine except the sync itself.</p>
    </div>
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
      showError(asSentence(err.detail) || "Couldn't connect. Double-check the token and try again.");
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
      ${stepHeadMarkup(3, "Bringing in your accounts")}
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
        <div class="onboarding-confirmed">
          ${ICON_CHECK}
          <p class="onboarding-body">${pluralize(accountCount, "account")},
            ${pluralize(txCount, "transaction")} — you're all set.</p>
        </div>
        <div class="onboarding-actions">
          <button type="button" class="btn btn-primary onboarding-cta" data-action="continue">Continue</button>
        </div>
      `;
      body.querySelector('[data-action="continue"]').addEventListener("click", () => ctx.goTo(4));
    })
    .catch((err) => {
      if (!body.isConnected) return;
      body.innerHTML = `
        <p class="onboarding-body">${escapeHtml(asSentence(err.detail) || "The sync didn't go through.")} No rush — try again
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
  const isLiability = acc.account_type === "liability";
  const institution = isLiability
    ? acc.issuer_name || acc.bank_name
    : acc.bank_name || acc.issuer_name;
  const kind = isLiability
    ? LIABILITY_LABELS[acc.liability_type]
    : ASSET_LABELS[acc.asset_type];
  return [institution, kind].filter(Boolean).join(" · ");
}

function renderAccounts(panel, ctx) {
  panel.innerHTML = `
    <div class="onboarding-step">
      ${stepHeadMarkup(4, "Meet your accounts")}
      <div id="accounts-body" class="onboarding-dynamic">
        <div class="onboarding-accounts">
          <div class="skeleton"></div>
          <div class="skeleton"></div>
          <div class="skeleton"></div>
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
            <div class="onboarding-account-id">
              <span class="onboarding-tile" aria-hidden="true">${
                acc.account_type === "liability" ? ICON_CARD : ICON_BANK
              }</span>
              <div class="onboarding-account-text">
                <p class="onboarding-account-name">${escapeHtml(acc.name || acc.display_name)}</p>
                <p class="onboarding-account-sub">${escapeHtml(accountSubtitle(acc))}</p>
              </div>
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
        <p class="onboarding-body">${escapeHtml(asSentence(err.detail) || "Couldn't load your accounts just now.")}</p>
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
      ${stepHeadMarkup(5, "You're set")}
      <div class="onboarding-pointers">
        <div class="onboarding-pointer">
          <span class="onboarding-tile" aria-hidden="true">${ICON_TRANSACTIONS_MINI}</span>
          <div>
            <p class="onboarding-pointer-title">Transactions</p>
            <p class="onboarding-pointer-body">Review what came in and categorize what matters.</p>
          </div>
        </div>
        <div class="onboarding-pointer">
          <span class="onboarding-tile" aria-hidden="true">${ICON_CATEGORIES_MINI}</span>
          <div>
            <p class="onboarding-pointer-title">Categories</p>
            <p class="onboarding-pointer-body">See where it's going, organized your way.</p>
          </div>
        </div>
        <div class="onboarding-pointer">
          <span class="onboarding-tile" aria-hidden="true">${ICON_SYNC_MINI}</span>
          <div>
            <p class="onboarding-pointer-title">Sync, anytime</p>
            <p class="onboarding-pointer-body">One click, top of the sidebar, whenever you want the latest.</p>
          </div>
        </div>
      </div>
      <div class="onboarding-updates">
        <p class="onboarding-pointer-title">Check for updates?</p>
        <p class="onboarding-pointer-body">BlitZave can ask GitHub once a day whether a newer version
          exists. It sends nothing about you or your money.</p>
        <div class="onboarding-updates-choices" id="update-choices">
          <button type="button" class="btn btn-ghost" data-update-choice="yes">Yes, check</button>
          <button type="button" class="btn btn-quiet" data-update-choice="no">No thanks</button>
        </div>
      </div>
      <div class="onboarding-actions">
        <button type="button" class="btn btn-primary onboarding-cta" data-action="finish">Take me in</button>
      </div>
    </div>
  `;
  panel.querySelector('[data-action="finish"]').addEventListener("click", finishOnboarding);
  wireUpdateChoice(panel.querySelector("#update-choices"));
}

// Neither answer is preselected. Skipping it, or onboarding, leaves checks
// off; the sidebar footer can turn them on any time.
function wireUpdateChoice(choices) {
  choices.addEventListener("click", (event) => {
    const choice = event.target.closest("[data-update-choice]")?.dataset.updateChoice;
    if (!choice) return;
    const enabled = choice === "yes";
    choices.querySelectorAll("button").forEach((b) => (b.disabled = true));
    api
      .setUpdateCheck(enabled)
      .then(() => {
        if (!choices.isConnected) return;
        choices.innerHTML = `<p class="onboarding-updates-done">${
          enabled
            ? "Got it. BlitZave will check once a day."
            : "Got it. You can turn this on later at the bottom of the sidebar."
        }</p>`;
      })
      .catch((err) => {
        if (!choices.isConnected) return;
        choices.innerHTML = `<p class="onboarding-updates-done">${escapeHtml(
          asSentence(err.detail) || "Couldn't save that just now."
        )} You can choose later at the bottom of the sidebar.</p>`;
      });
  });
}

// ------------------------------ Shared bits ------------------------------

function wireSkip(panel, ctx) {
  const skipBtn = panel.querySelector('[data-action="skip"]');
  if (skipBtn) skipBtn.addEventListener("click", () => ctx.skip());
}
