// BlitZave — Savings (#/savings). Goals, grouped by the goal account they
// live in (design canvas "BlitZave — Home & Savings", third pass).
//
//   Critical  one card per goal: it has its account to itself, so the card
//             is the goal and the account together (ring + account footer).
//   Regular   one card per shared account: its goals in the order money
//             fills them, top first, with a rail showing how far it reaches.
//   Free      goal accounts with no goal yet, ready for one.
//
// Above them, a question card when money left a goal account and the amount
// is close to what a funded goal needed: "Was that the PS5?". Each is asked
// once; the engine decides which (refine/goals.goal_questions).
//
// The engine decides every number: who gets what (refine/goals.py), the
// percentages, every *_display string. This page only lays them out. Cents
// are used only for geometry (how far a rail runs). Reordering asks the
// engine and redraws from its answer, so the split on screen is always the
// one the engine computed.

// Versioned import: a tab left open across an update still holds the old
// api.js, which lacks the goal and export calls (LESSONS.md 13).
import * as api from "../api.js?v=goals";

const STATE_LABELS = { backlog: "Not started", in_progress: "Saving", done: "Funded" };

const MONTHS_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

const ICON_PLUS = `
  <svg class="savings-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>
`;
const ICON_SHIELD = `
  <svg class="savings-icon" viewBox="0 0 24 24" aria-hidden="true">
    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
  </svg>
`;
const ICON_LAYERS = `
  <svg class="savings-icon" viewBox="0 0 24 24" aria-hidden="true">
    <path d="m12 2 10 5-10 5L2 7z" /><path d="m2 17 10 5 10-5" /><path d="m2 12 10 5 10-5" />
  </svg>
`;
const ICON_BANK = `
  <svg class="savings-icon" viewBox="0 0 24 24" aria-hidden="true">
    <path d="M3 21h18" /><path d="M5 21v-9M9.5 21v-9M14.5 21v-9M19 21v-9" /><path d="M12 3 3 8.5h18z" />
  </svg>
`;
const ICON_ORDER = `
  <svg class="savings-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m7 15 5 5 5-5" /><path d="m7 9 5-5 5 5" /></svg>
`;
const ICON_UP = `
  <svg class="savings-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m6 15 6-6 6 6" /></svg>
`;
const ICON_DOWN = `
  <svg class="savings-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6" /></svg>
`;
const ICON_CHECK = `
  <svg class="savings-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M20 6 9 17l-5-5" /></svg>
`;

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// "2027-05-06" → "May 6, 2027" (calendar text, not money).
function longDate(iso) {
  const [y, m, d] = String(iso || "").split("-").map(Number);
  return y && m && d ? `${MONTHS[m - 1]} ${d}, ${y}` : "";
}

function shortDate(iso) {
  const [, m, d] = String(iso || "").split("-").map(Number);
  return m && d ? `${MONTHS_SHORT[m - 1]} ${d}` : "";
}

function scheduleText(goal) {
  if (goal.schedule === "once" && goal.target_on) return `By ${longDate(goal.target_on)}`;
  if (goal.schedule === "recurring") {
    const every = goal.period_months === 1 ? "Every month" : `Every ${goal.period_months} months`;
    return goal.ends_on ? `${every}, until ${longDate(goal.ends_on)}` : every;
  }
  return "";
}

function accountLine(home) {
  return home.account_number ? `${home.name} · Ending ${home.account_number}` : home.name;
}

// The engine rounds to one place; never show 100% for a goal that isn't
// funded yet.
function percentText(goal) {
  const whole = Math.floor(goal.saved_percent);
  return `${goal.state !== "done" && whole >= 100 ? 99 : whole}%`;
}

function stateChip(goal) {
  return `<span class="goal-state goal-state--${goal.state}">${STATE_LABELS[goal.state]}</span>`;
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

export async function render(root) {
  root.innerHTML = `
    <div class="savings-page">
      <header class="savings-intro">
        <p class="eyebrow">Savings</p>
        <div class="skeleton" style="width: 220px; height: 2.6em; margin-top: 8px;"></div>
        <div class="skeleton skeleton-text" style="width: 280px; margin-top: 12px;"></div>
      </header>
    </div>
  `;

  let view;
  try {
    view = await api.listGoals();
  } catch (err) {
    root.innerHTML = `
      <div class="empty-state">
        <p class="empty-state-title">Couldn't load your savings</p>
        <p class="empty-state-body">${escapeHtml((err && err.detail) || "Something went wrong reaching BlitZave.")}</p>
        <button class="btn btn-ghost" type="button" id="savings-retry">Try again</button>
      </div>
    `;
    root.querySelector("#savings-retry").addEventListener("click", () => render(root));
    return;
  }

  if (!view.accounts.length) {
    renderNoGoalAccounts(root);
    return;
  }
  renderSavings(root, view);
}

// Before any account is a goal account, the page teaches the one idea that
// makes the rest work: goals live in their own accounts.
function renderNoGoalAccounts(root) {
  root.innerHTML = `
    <div class="savings-page">
      <header class="savings-intro">
        <p class="eyebrow">Savings</p>
        <h1 class="savings-intro-title">Give your goals a place to live</h1>
        <p class="savings-intro-lede">
          Goals live in goal accounts: savings accounts at your bank that hold money for
          something specific. A critical goal, like an emergency fund, gets an account to
          itself. Smaller goals, like a trip or a new phone, can share one.
        </p>
      </header>
      <div class="savings-empty card">
        <span class="savings-empty-mark" aria-hidden="true">${ICON_BANK}</span>
        <div class="savings-empty-text">
          <p class="savings-empty-title">Pick an account for your goals</p>
          <p class="savings-empty-body">
            On Accounts, open a savings account's menu and choose <strong>Goal account</strong>.
            It shows up here, ready for its first goal.
          </p>
        </div>
        <a class="btn btn-primary" href="#/accounts">Go to Accounts</a>
      </div>
    </div>
  `;
}

function renderSavings(root, view) {
  const critical = view.accounts.filter(
    (home) => home.goals.length === 1 && home.goals[0].goal_level === "critical"
  );
  const shared = view.accounts.filter(
    (home) => home.goals.length && !critical.includes(home)
  );
  const free = view.accounts.filter((home) => !home.goals.length);
  const thisYear = String(new Date().getFullYear());
  const completed = view.completed.filter((goal) => goal.completed_on.startsWith(thisYear));
  const count = view.accounts.length;

  root.innerHTML = `
    <div class="savings-page">
      <header class="savings-intro savings-intro--hero">
        <div>
          <p class="eyebrow">Savings</p>
          <p class="figure-hero savings-total u-tabular">${escapeHtml(view.total_display)}</p>
          <h1 class="savings-intro-sub">Set aside across ${count} goal ${count === 1 ? "account" : "accounts"}</h1>
        </div>
        <a class="btn btn-primary" href="#/savings/new">${ICON_PLUS}<span>New goal</span></a>
      </header>
      <div class="savings-sections"></div>
    </div>
  `;
  const sections = root.querySelector(".savings-sections");
  (view.questions || []).forEach((question) => sections.appendChild(questionCard(root, question)));

  if (!critical.length && !shared.length) {
    const hint = document.createElement("p");
    hint.className = "savings-hint";
    hint.textContent = "Your goal accounts are ready. Give one its first goal.";
    sections.appendChild(hint);
  }

  if (critical.length) {
    const section = sectionEl("Critical", ICON_SHIELD, "savings-critical-h");
    const grid = document.createElement("div");
    grid.className = "savings-critical-grid";
    critical.forEach((home) => grid.insertAdjacentHTML("beforeend", criticalCardMarkup(home)));
    section.appendChild(grid);
    sections.appendChild(section);
  }

  if (shared.length) {
    const section = sectionEl("Regular", ICON_LAYERS, "savings-regular-h");
    shared.forEach((home) => section.appendChild(sharedCard(home)));
    sections.appendChild(section);
  }

  if (free.length) {
    const section = sectionEl("Ready for a goal", ICON_BANK, "savings-free-h");
    free.forEach((home) => section.insertAdjacentHTML("beforeend", freeStripMarkup(home)));
    sections.appendChild(section);
  }

  if (completed.length) {
    const section = sectionEl("Completed this year", ICON_CHECK, "savings-done-h");
    const list = document.createElement("ul");
    list.className = "savings-done-list";
    list.innerHTML = completed.map(completedMarkup).join("");
    section.appendChild(list);
    sections.appendChild(section);
  }
}

function sectionEl(title, icon, id) {
  const section = document.createElement("section");
  section.className = "savings-section";
  section.setAttribute("aria-labelledby", id);
  section.innerHTML = `<h2 class="savings-section-title" id="${id}">${icon}<span>${escapeHtml(title)}</span></h2>`;
  return section;
}

// --- critical: the goal and its account are one card ---------------------------

const RING_R = 38;
const RING_C = 2 * Math.PI * RING_R;

function ringMarkup(goal) {
  const filled = (Math.min(goal.saved_percent, 100) / 100) * RING_C;
  return `
    <svg class="goal-ring goal-ring--${goal.state}" viewBox="0 0 96 96" role="img"
         aria-label="${escapeHtml(percentText(goal))} of the goal">
      <circle class="goal-ring-track" cx="48" cy="48" r="${RING_R}" />
      <circle class="goal-ring-fill" cx="48" cy="48" r="${RING_R}"
              stroke-dasharray="${filled.toFixed(1)} ${RING_C.toFixed(1)}" transform="rotate(-90 48 48)" />
      <text class="goal-ring-text" x="48" y="54" text-anchor="middle">${escapeHtml(percentText(goal))}</text>
    </svg>
  `;
}

function criticalLine(goal) {
  if (goal.state === "done") return "Fully funded. It's there whenever you need it.";
  if (goal.state === "in_progress") return `${goal.to_go_display} to go.`;
  return "Ready for its first deposit.";
}

function criticalCardMarkup(home) {
  const goal = home.goals[0];
  const when = scheduleText(goal);
  return `
    <a class="card card-interactive goal-vault" href="#/savings/${goal.id}">
      <div class="goal-vault-body">
        ${ringMarkup(goal)}
        <div class="goal-vault-text">
          <div class="goal-vault-head">
            <span class="goal-vault-name">${escapeHtml(goal.name)}</span>
            ${stateChip(goal)}
          </div>
          <p class="goal-amounts u-tabular">
            <span class="goal-amounts-have">${escapeHtml(goal.saved_display)}</span>
            <span class="goal-amounts-of">of ${escapeHtml(goal.budget_display)}</span>
          </p>
          <p class="goal-vault-line">${escapeHtml(criticalLine(goal))}${when ? ` <span class="goal-when">${escapeHtml(when)}</span>` : ""}</p>
          ${goal.pace_line ? `<p class="goal-pace">${escapeHtml(goal.pace_line)}</p>` : ""}
        </div>
      </div>
      <div class="goal-vault-account">${ICON_BANK}<span>${escapeHtml(accountLine(home))}</span></div>
    </a>
  `;
}

// --- regular: a shared account and the line its money fills -----------------------

function regularHint(goal, above) {
  if (goal.state === "done") return "Ready when you are";
  if (goal.state === "in_progress") return `${goal.to_go_display} to go`;
  return above ? `Fills after ${above.name}` : "Fills first";
}

function sharedCard(home) {
  const card = document.createElement("div");
  card.className = "card goal-pool";
  drawSharedCard(card, home, false);
  return card;
}

function drawSharedCard(card, home, ordering, announcement = "") {
  const n = home.goals.length;
  const canOrder = n > 1;
  card.innerHTML = `
    <div class="goal-pool-head">
      <div class="goal-pool-id">
        <span class="goal-pool-mark" aria-hidden="true">${ICON_BANK}</span>
        <div>
          <p class="goal-pool-name">${escapeHtml(home.name)}</p>
          <p class="goal-pool-meta">${escapeHtml(
            [home.account_number && `Ending ${home.account_number}`, `shared by ${n} ${n === 1 ? "goal" : "goals"}`]
              .filter(Boolean)
              .join(" · ")
          )}</p>
        </div>
      </div>
      <div class="goal-pool-side">
        <span class="goal-pool-balance u-tabular">${escapeHtml(home.balance_display ?? "No balance yet")}</span>
        ${
          ordering
            ? `<button type="button" class="btn btn-primary" data-action="done">Done</button>`
            : canOrder
              ? `<button type="button" class="btn btn-ghost" data-action="order">${ICON_ORDER}<span>Change order</span></button>`
              : ""
        }
      </div>
    </div>
    ${
      ordering
        ? `<p class="goal-pool-explain">Money here fills the top goal first, then flows down to the next. Moving a goal moves the money with it, right away.</p>`
        : ""
    }
    <ol class="goal-line${ordering ? " is-ordering" : ""}" aria-label="Goals in ${escapeHtml(home.name)}, in the order they fill">
      ${home.goals.map((goal, i) => lineRowMarkup(home, goal, i, ordering)).join("")}
    </ol>
    ${
      home.unassigned_cents > 0
        ? `<p class="goal-pool-left u-tabular"><strong>${escapeHtml(home.unassigned_display)}</strong> more than these goals need. It goes to the next goal you add here.</p>`
        : ""
    }
    ${
      ordering
        ? ""
        : `<div class="goal-pool-add"><a class="goal-add-here" href="#/savings/new?account=${encodeURIComponent(home.id)}&level=regular">${ICON_PLUS}<span>Add a goal here</span></a></div>`
    }
    <p class="sr-only" aria-live="polite">${escapeHtml(announcement)}</p>
  `;

  card.querySelector('[data-action="order"]')?.addEventListener("click", () => {
    drawSharedCard(card, home, true);
    card.querySelector(".goal-move:not([disabled])")?.focus();
  });
  card.querySelector('[data-action="done"]')?.addEventListener("click", () => {
    drawSharedCard(card, home, false);
  });
  card.querySelectorAll(".goal-move").forEach((btn) => {
    btn.addEventListener("click", () => move(card, home, Number(btn.dataset.index), Number(btn.dataset.step)));
  });
}

async function move(card, home, index, step) {
  const ids = home.goals.map((goal) => goal.id);
  const target = index + step;
  if (target < 0 || target >= ids.length) return;
  [ids[index], ids[target]] = [ids[target], ids[index]];
  card.querySelectorAll(".goal-move").forEach((btn) => {
    btn.disabled = true;
  });
  try {
    const updated = await api.reorderGoals(home.id, ids);
    Object.assign(home, updated);
    const movedGoal = home.goals[target];
    drawSharedCard(card, home, true, `${movedGoal.name} is now number ${target + 1}.`);
    const again = card.querySelector(`.goal-move[data-index="${target}"][data-step="${step}"]:not([disabled])`)
      || card.querySelector(`.goal-move[data-index="${target}"]:not([disabled])`);
    again?.focus();
  } catch (err) {
    // A 409 means the line changed elsewhere (a goal was added or bought):
    // start again from what the engine has now.
    showToast(
      err && err.status === 409
        ? "This line changed since the page loaded. Here it is as it stands now."
        : (err && err.detail) || "Couldn't change the order. Try again.",
      err && err.status === 409 ? "default" : "danger"
    );
    try {
      const fresh = (await api.listGoals()).accounts.find((a) => a.id === home.id);
      if (fresh) Object.assign(home, fresh);
    } catch {
      // Keep what's on screen; the toast already said what happened.
    }
    drawSharedCard(card, home, true);
  }
}

function lineRowMarkup(home, goal, i, ordering) {
  const goals = home.goals;
  const above = i > 0 ? goals[i - 1] : null;
  const next = goals[i + 1];
  // How far the money reaches, for the rail: it touches a goal that has
  // anything, and runs past one that's full while money still flows on.
  const reached = goal.saved_cents > 0;
  const through =
    goal.state === "done" && (next ? next.saved_cents > 0 : home.unassigned_cents > 0);
  const width = goal.saved_cents > 0 ? Math.max(2, Math.min(100, goal.saved_percent)) : 0;
  const when = scheduleText(goal);
  const name = ordering
    ? `<span class="goal-row-name">${escapeHtml(goal.name)}</span>`
    : `<a class="goal-row-name" href="#/savings/${goal.id}">${escapeHtml(goal.name)}</a>`;
  const moves = ordering
    ? `<span class="goal-moves">
        <button type="button" class="btn btn-ghost goal-move" data-index="${i}" data-step="-1"
                aria-label="Move ${escapeHtml(goal.name)} up" ${i === 0 ? "disabled" : ""}>${ICON_UP}</button>
        <button type="button" class="btn btn-ghost goal-move" data-index="${i}" data-step="1"
                aria-label="Move ${escapeHtml(goal.name)} down" ${i === goals.length - 1 ? "disabled" : ""}>${ICON_DOWN}</button>
      </span>`
    : "";
  return `
    <li class="goal-row">
      <span class="goal-rail" aria-hidden="true">
        <span class="goal-rail-seg${reached ? " is-filled" : ""}"></span>
        <span class="goal-rail-dot${reached && !through ? " is-on" : ""}"></span>
        <span class="goal-rail-seg${through ? " is-filled" : ""}"></span>
      </span>
      <span class="goal-row-n" aria-hidden="true">${i + 1}</span>
      <div class="goal-row-body">
        <div class="goal-row-head">
          ${name}
          <span class="goal-row-amounts u-tabular"><strong>${escapeHtml(goal.saved_display)}</strong> of ${escapeHtml(goal.budget_display)}</span>
          ${moves}
        </div>
        <div class="goal-bar" role="img" aria-label="${escapeHtml(`${goal.saved_display} of ${goal.budget_display}`)}">
          <div class="goal-bar-fill goal-bar-fill--${goal.state}" style="width: ${width}%"></div>
        </div>
        <div class="goal-row-foot">
          ${stateChip(goal)}
          <span class="goal-row-hint">${escapeHtml(regularHint(goal, above))}</span>
          ${when ? `<span class="goal-when">${escapeHtml(when)}</span>` : ""}
        </div>
        ${goal.pace_line && !ordering ? `<p class="goal-pace">${escapeHtml(goal.pace_line)}</p>` : ""}
      </div>
    </li>
  `;
}

// --- "Was that the PS5?" ---------------------------------------------------------

function questionCard(root, question) {
  const card = document.createElement("section");
  card.className = "card goal-question";
  card.setAttribute("aria-label", `Was that the ${question.goal_name}?`);
  card.innerHTML = `
    <p class="goal-question-where">${escapeHtml(question.account_name)} · ${escapeHtml(longDate(question.posted_on))}</p>
    <p class="goal-question-text">
      <span class="u-tabular">${escapeHtml(question.amount_display)}</span> left this account.
      Was that the ${escapeHtml(question.goal_name)}?
    </p>
    <div class="goal-question-actions">
      <button type="button" class="btn btn-primary" data-answer="bought">Yes, I bought it</button>
      <button type="button" class="btn btn-ghost" data-answer="other">It was something else</button>
    </div>
  `;
  card.querySelectorAll("[data-answer]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      card.querySelectorAll("[data-answer]").forEach((b) => {
        b.disabled = true;
      });
      try {
        const view = await api.answerGoalQuestion(
          question.transaction_id, question.goal_id, btn.dataset.answer
        );
        showToast(
          btn.dataset.answer === "bought"
            ? `Enjoy the ${question.goal_name}. It's under Completed this year, and the goals below it move up.`
            : "Got it. That money comes off the bottom of the line."
        );
        renderSavings(root, view);
      } catch (err) {
        // A 409 means it was answered elsewhere or the goal changed: show
        // the page as it is now.
        showToast(
          err && err.status === 409
            ? "That one's already settled. Here's where things stand."
            : (err && err.detail) || "Couldn't save that answer. Try again.",
          err && err.status === 409 ? "default" : "danger"
        );
        render(root);
      }
    })
  );
  return card;
}

// --- free accounts and completed goals ---------------------------------------------

function freeStripMarkup(home) {
  const holds = home.balance_display
    ? ` holds <strong class="u-tabular">${escapeHtml(home.balance_display)}</strong> with no goal yet.`
    : " has no goal yet.";
  return `
    <div class="goal-free">
      <div class="goal-free-text">
        <span class="goal-pool-mark" aria-hidden="true">${ICON_BANK}</span>
        <p><strong>${escapeHtml(home.name)}</strong>${home.account_number ? ` · Ending ${escapeHtml(home.account_number)}` : ""}${holds}</p>
      </div>
      <a class="link goal-free-action" href="#/savings/new?account=${encodeURIComponent(home.id)}">Give it a goal</a>
    </div>
  `;
}

function completedMarkup(goal) {
  return `
    <li class="goal-done">
      <span class="goal-done-mark" aria-hidden="true">${ICON_CHECK}</span>
      <span class="goal-done-text">
        <a class="goal-done-name" href="#/savings/${goal.id}">${escapeHtml(goal.name)}</a>
        <span class="goal-done-meta u-tabular">${escapeHtml(goal.budget_display)} · bought ${escapeHtml(shortDate(goal.completed_on))}</span>
      </span>
    </li>
  `;
}
