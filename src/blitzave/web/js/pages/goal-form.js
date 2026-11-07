// BlitZave — a goal, new (#/savings/new) or existing (#/savings/:id).
//
// The form asks in the order that keeps goals healthy (design canvas, third
// pass): what it is → how important → where it lives → when. The account
// list does the teaching. A critical goal can only pick an empty goal
// account, and one that's taken shows who holds it, so the rules are visible
// before anyone trips over them. Strict (maintainer call, 2026-11-06): with
// no empty goal account, a critical goal can't be saved. The engine's 409s
// are the backstop, not the guide.
//
// Query params for a new goal: ?account=ACT-…&level=regular|critical.
// Money is never computed here: the amount goes to the engine as typed, and
// every figure shown is a *_display string from GET /api/goals.

import * as api from "../api.js";
import { navigate } from "../router.js";

const LEVELS = [
  {
    value: "critical",
    title: "Critical",
    body: "Can't go without it. An emergency fund, months of runway, a down payment, education.",
  },
  { value: "regular", title: "Regular", body: "Nice to have. Trips, gadgets, treats." },
];
const PERIODS = [1, 2, 3, 4, 6, 12];
const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function isoDate(d) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function sixMonthsOut() {
  const d = new Date();
  d.setMonth(d.getMonth() + 6);
  return isoDate(d);
}

function longDate(iso) {
  const [y, m, d] = String(iso || "").split("-").map(Number);
  return y && m && d ? `${MONTHS[m - 1]} ${d}, ${y}` : "";
}

function joinNames(names) {
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
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

// The engine's refusals, said the way a friend would.
function friendlyError(err) {
  const detail = err && err.detail;
  if (Array.isArray(detail)) {
    const text = detail.map((d) => d.msg || "").join(" ");
    if (/budget|amount|cents/i.test(text)) {
      return "That amount didn't read as money. Try something like 1200 or 1,200.50.";
    }
    return "Something in the form didn't add up. Check each field and try again.";
  }
  const text = String(detail || "");
  if (text.includes("to itself")) {
    return "That account holds another goal now. A critical goal needs an account to itself.";
  }
  if (text.includes("targeted savings")) {
    return "That account isn't a goal account anymore. Pick another one.";
  }
  if (text.includes("already exists")) {
    return "You already have a category with that name. Give this goal a different one.";
  }
  if (text.includes("still used by transactions")) {
    return "Some transactions are filed under this goal. Recategorize them first, then delete it.";
  }
  return text || "Couldn't save that. Try again.";
}

export async function render(root, params = {}) {
  root.innerHTML = `
    <div class="goal-form-page">
      <div class="skeleton" style="width: 40%; height: 2em;"></div>
      <div class="skeleton skeleton-text" style="width: 60%; margin-top: 16px;"></div>
    </div>
  `;

  let view;
  try {
    view = await api.listGoals();
  } catch (err) {
    root.innerHTML = `
      <div class="empty-state">
        <p class="empty-state-title">Couldn't load your goal accounts</p>
        <p class="empty-state-body">${escapeHtml((err && err.detail) || "Something went wrong reaching BlitZave.")}</p>
        <a class="btn btn-ghost" href="#/savings">Back to Savings</a>
      </div>
    `;
    return;
  }

  if (params.id === undefined) {
    renderForm(root, view, null, params);
    return;
  }

  const id = Number(params.id);
  const active = view.accounts.flatMap((home) => home.goals).find((g) => g.id === id);
  const done = view.completed.find((g) => g.id === id);
  if (active) renderForm(root, view, active, params);
  else if (done) renderCompleted(root, done);
  else {
    root.innerHTML = `
      <div class="empty-state">
        <p class="empty-state-title">That goal isn't here anymore</p>
        <p class="empty-state-body">It may have been deleted, or turned back into a plain category.</p>
        <a class="btn btn-ghost" href="#/savings">Back to Savings</a>
      </div>
    `;
  }
}

// --- the form ---------------------------------------------------------------------

function renderForm(root, view, goal, params) {
  const editing = goal !== null;
  const state = editing
    ? {
        name: goal.name,
        amount: goal.budget_display,
        level: goal.goal_level,
        account: goal.account_id,
        mode: goal.schedule === "recurring" ? "recurring" : "once",
        target: goal.target_on || sixMonthsOut(),
        period: goal.period_months || 3,
        end: goal.ends_on || "",
      }
    : {
        name: "",
        amount: "",
        level: params.level === "critical" || params.level === "regular" ? params.level : null,
        account: params.account || null,
        mode: "once",
        target: sixMonthsOut(),
        period: 3,
        end: "",
      };
  const selfId = editing ? goal.id : null;
  let saving = false;
  let error = "";

  // What each account can take, worked out from who already lives there.
  function homes() {
    return view.accounts.map((home) => {
      const others = home.goals.filter((g) => g.id !== selfId);
      const critical = others.find((g) => g.goal_level === "critical");
      let note;
      if (critical) note = `${critical.name}'s own account`;
      else if (others.length) note = `Shared by ${joinNames(others.map((g) => g.name))}`;
      else if (home.balance_display) note = `No goals yet. Its ${home.balance_display} counts toward this one.`;
      else note = "No goals yet.";
      const open = state.level === "critical" ? others.length === 0 : !critical;
      return { home, others, note, open };
    });
  }

  function placeNote(choices) {
    const chosen = choices.find((c) => c.home.id === state.account && c.open);
    if (!chosen || state.level !== "regular") return "";
    if (editing && goal.account_id === state.account) return "It keeps its place in line.";
    if (!chosen.others.length) return "It's the only goal here, so it fills first.";
    return `It joins the end of the line, after ${joinNames(chosen.others.map((g) => g.name))}. You can change the order on Savings.`;
  }

  function draw() {
    const choices = homes();
    // A level change can close the account that was picked.
    if (state.account && !choices.some((c) => c.home.id === state.account && c.open)) {
      if (state.level) state.account = null;
    }
    const anyOpen = choices.some((c) => c.open);
    const canSave =
      !saving && state.level && state.account && anyOpen && state.name.trim() && state.amount.trim();
    const focusedId = document.activeElement && document.activeElement.id;

    root.innerHTML = `
      <div class="goal-form-page">
        <header class="savings-intro">
          <p class="eyebrow">${editing ? "Goal" : "New goal"}</p>
          <h1 class="savings-intro-title">${editing ? escapeHtml(goal.name) : "Something you're saving toward"}</h1>
          ${editing ? `<p class="savings-intro-lede u-tabular">${escapeHtml(goal.saved_display)} of ${escapeHtml(goal.budget_display)} so far.</p>` : ""}
        </header>

        <form class="goal-form card" novalidate>
          <div class="goal-form-row">
            <label class="field">
              <span class="field-label">Name</span>
              <input class="input" id="goal-name" autocomplete="off" maxlength="80"
                     placeholder="PS5, Japan trip, emergency fund" value="${escapeHtml(state.name)}" />
            </label>
            <label class="field goal-form-amount">
              <span class="field-label">Amount</span>
              <span class="goal-money-input">
                <span class="goal-money-prefix" aria-hidden="true">$</span>
                <input class="input u-tabular" id="goal-amount" inputmode="decimal" autocomplete="off"
                       placeholder="1,200" value="${escapeHtml(String(state.amount).replace(/^\$/, ""))}" />
              </span>
            </label>
          </div>

          <fieldset class="goal-form-group">
            <legend class="field-label">How important is it?</legend>
            <div class="goal-choice-grid">
              ${LEVELS.map(
                (l) => `
                <label class="goal-choice${state.level === l.value ? " is-selected" : ""}">
                  <input type="radio" name="goal-level" id="goal-level-${l.value}" value="${l.value}"
                         ${state.level === l.value ? "checked" : ""} />
                  <span class="goal-choice-title">${l.title}</span>
                  <span class="goal-choice-body">${l.body}</span>
                </label>`
              ).join("")}
            </div>
          </fieldset>

          ${state.level ? homeGroupMarkup(choices, anyOpen) : ""}

          ${
            state.level && anyOpen
              ? `
          <fieldset class="goal-form-group">
            <legend class="field-label">When</legend>
            <div class="goal-segmented" role="radiogroup" aria-label="When">
              <button type="button" role="radio" id="goal-mode-once" aria-checked="${state.mode === "once"}"
                      class="goal-segment${state.mode === "once" ? " is-selected" : ""}" data-mode="once">One time</button>
              <button type="button" role="radio" id="goal-mode-recurring" aria-checked="${state.mode === "recurring"}"
                      class="goal-segment${state.mode === "recurring" ? " is-selected" : ""}" data-mode="recurring">Recurring</button>
            </div>
            ${
              state.mode === "once"
                ? `
              <label class="field goal-form-date">
                <span class="field-label">Target date</span>
                <input class="input" type="date" id="goal-target" value="${escapeHtml(state.target)}" />
                ${editing ? "" : `<span class="field-hint">Starts six months out. Change it to whenever you need it.</span>`}
              </label>`
                : `
              <div class="field">
                <span class="field-label">Every</span>
                <div class="goal-periods">
                  ${PERIODS.map(
                    (p) => `<button type="button" class="chip goal-period${state.period === p ? " chip-accent" : ""}"
                                    id="goal-period-${p}" data-period="${p}" aria-pressed="${state.period === p}">
                              ${p === 1 ? "month" : `${p} months`}</button>`
                  ).join("")}
                </div>
                <span class="field-hint">Rounds follow the calendar, so ${state.period === 12 ? "each round is a calendar year" : state.period === 1 ? "each round is a calendar month" : `a ${state.period}-month round starts in January`}.</span>
              </div>
              <label class="field goal-form-date">
                <span class="field-label">End date (optional)</span>
                <input class="input" type="date" id="goal-end" value="${escapeHtml(state.end)}" />
              </label>`
            }
          </fieldset>`
              : ""
          }

          ${error ? `<p class="field-error goal-form-error" role="alert">${escapeHtml(error)}</p>` : ""}

          <div class="goal-form-actions">
            ${
              editing
                ? `<div class="goal-form-secondary">
                    <button type="button" class="btn btn-ghost" id="goal-bought">Mark as bought</button>
                    <button type="button" class="btn btn-quiet goal-delete" id="goal-delete">Delete goal</button>
                  </div>`
                : "<span></span>"
            }
            <div class="goal-form-primary">
              <a class="btn btn-quiet" href="#/savings">Cancel</a>
              <button type="submit" class="btn btn-primary" id="goal-save" ${canSave ? "" : "disabled"}>
                ${saving ? "Saving…" : editing ? "Save changes" : "Save goal"}
              </button>
            </div>
          </div>
        </form>
      </div>
    `;

    wire();
    if (focusedId) document.getElementById(focusedId)?.focus();
  }

  function homeGroupMarkup(choices, anyOpen) {
    const blocked =
      state.level === "critical"
        ? `Every goal account already holds goals, and a critical goal needs one to itself.
           Open a new savings account at your bank, or pick one you already have, and mark it
           as a <strong>Goal account</strong> on <a class="link" href="#/accounts">Accounts</a>.
           It shows up here after that.`
        : `Every goal account here belongs to a critical goal. Mark another savings account
           as a <strong>Goal account</strong> on <a class="link" href="#/accounts">Accounts</a>
           to give this goal a home.`;
    const place = placeNote(choices);
    return `
      <fieldset class="goal-form-group">
        <legend class="field-label">Where it lives</legend>
        <div class="goal-homes">
          ${choices
            .map(
              (c) => `
            <label class="goal-home${c.open ? "" : " is-closed"}${state.account === c.home.id && c.open ? " is-selected" : ""}">
              <input type="radio" name="goal-home" id="goal-home-${escapeHtml(c.home.id)}" value="${escapeHtml(c.home.id)}"
                     ${state.account === c.home.id && c.open ? "checked" : ""} ${c.open ? "" : "disabled"} />
              <span class="goal-home-text">
                <span class="goal-home-name">${escapeHtml(c.home.name)}${
                  c.home.account_number ? ` <span class="goal-home-last4">· ${escapeHtml(c.home.account_number)}</span>` : ""
                }</span>
                <span class="goal-home-note">${escapeHtml(c.note)}</span>
              </span>
              <span class="goal-home-balance u-tabular">${escapeHtml(c.home.balance_display ?? "")}</span>
            </label>`
            )
            .join("")}
        </div>
        ${anyOpen ? "" : `<p class="goal-form-blocked">${blocked}</p>`}
        ${place ? `<p class="field-hint goal-form-place">${escapeHtml(place)}</p>` : ""}
      </fieldset>
    `;
  }

  function wire() {
    const form = root.querySelector(".goal-form");
    const saveBtn = root.querySelector("#goal-save");
    const refreshSave = () => {
      const choices = homes();
      saveBtn.disabled = !(
        !saving && state.level && state.account && choices.some((c) => c.open) &&
        state.name.trim() && state.amount.trim()
      );
    };

    root.querySelector("#goal-name").addEventListener("input", (e) => {
      state.name = e.target.value;
      refreshSave();
    });
    root.querySelector("#goal-amount").addEventListener("input", (e) => {
      state.amount = e.target.value;
      refreshSave();
    });
    root.querySelectorAll('input[name="goal-level"]').forEach((input) =>
      input.addEventListener("change", () => {
        state.level = input.value;
        error = "";
        draw();
      })
    );
    root.querySelectorAll('input[name="goal-home"]').forEach((input) =>
      input.addEventListener("change", () => {
        state.account = input.value;
        error = "";
        draw();
      })
    );
    root.querySelectorAll("[data-mode]").forEach((btn) =>
      btn.addEventListener("click", () => {
        state.mode = btn.dataset.mode;
        draw();
      })
    );
    root.querySelectorAll("[data-period]").forEach((btn) =>
      btn.addEventListener("click", () => {
        state.period = Number(btn.dataset.period);
        draw();
      })
    );
    root.querySelector("#goal-target")?.addEventListener("input", (e) => {
      state.target = e.target.value;
    });
    root.querySelector("#goal-end")?.addEventListener("input", (e) => {
      state.end = e.target.value;
    });

    form.addEventListener("submit", (e) => {
      e.preventDefault();
      save();
    });
    root.querySelector("#goal-bought")?.addEventListener("click", markBought);
    root.querySelector("#goal-delete")?.addEventListener("click", remove);
  }

  async function save() {
    if (state.mode === "once" && !state.target) {
      error = "Pick a target date, or switch to Recurring.";
      draw();
      return;
    }
    const plan =
      state.mode === "once"
        ? { schedule: "once", target_on: state.target }
        : { schedule: "recurring", period_months: state.period, ...(state.end ? { ends_on: state.end } : {}) };
    const body = {
      budget_amount: state.amount.trim(),
      ...plan,
      goal_level: state.level,
      account_id: state.account,
    };
    saving = true;
    error = "";
    draw();
    try {
      if (editing) {
        await api.updateCategory(goal.id, { name: state.name.trim(), ...body });
        showToast(`${state.name.trim()} is saved.`);
      } else {
        await api.createCategory(state.name.trim(), "savings", body);
        showToast(`${state.name.trim()} has a home.`);
      }
      navigate("/savings");
    } catch (err) {
      saving = false;
      error = friendlyError(err);
      // An account may have filled up since the page loaded: show it as it is now.
      if (err && err.status === 409) {
        try {
          view = await api.listGoals();
        } catch {
          // Keep the list we have.
        }
      }
      draw();
    }
  }

  async function markBought() {
    try {
      await api.updateCategory(goal.id, { completed_on: isoDate(new Date()) });
      showToast(`${goal.name} is marked as bought. Its account is free for what's next.`);
      navigate("/savings");
    } catch (err) {
      error = friendlyError(err);
      draw();
    }
  }

  async function remove() {
    if (!window.confirm(`Delete ${goal.name}? The money stays in its account; only the goal goes.`)) return;
    try {
      await api.deleteCategory(goal.id);
      showToast(`${goal.name} is deleted.`);
      navigate("/savings");
    } catch (err) {
      error = friendlyError(err);
      draw();
    }
  }

  draw();
}

// --- a completed goal --------------------------------------------------------------

function renderCompleted(root, goal) {
  root.innerHTML = `
    <div class="goal-form-page">
      <header class="savings-intro">
        <p class="eyebrow">Completed</p>
        <h1 class="savings-intro-title">${escapeHtml(goal.name)}</h1>
        <p class="savings-intro-lede u-tabular">${escapeHtml(goal.budget_display)}, bought ${escapeHtml(longDate(goal.completed_on))}.</p>
      </header>
      <div class="goal-form card">
        <p class="goal-form-note">It no longer shares its account's money. If it wasn't bought after all, it can go back to saving at the end of its account's line.</p>
        <p class="field-error goal-form-error" role="alert" hidden></p>
        <div class="goal-form-actions">
          <button type="button" class="btn btn-quiet goal-delete" id="goal-delete">Delete goal</button>
          <div class="goal-form-primary">
            <a class="btn btn-quiet" href="#/savings">Back</a>
            <button type="button" class="btn btn-ghost" id="goal-reopen">Back to saving</button>
          </div>
        </div>
      </div>
    </div>
  `;
  const errorEl = root.querySelector(".goal-form-error");
  const fail = (err) => {
    errorEl.hidden = false;
    errorEl.textContent = friendlyError(err);
  };
  root.querySelector("#goal-reopen").addEventListener("click", async () => {
    try {
      await api.updateCategory(goal.id, { completed_on: null });
      showToast(`${goal.name} is saving again.`);
      navigate("/savings");
    } catch (err) {
      fail(err);
    }
  });
  root.querySelector("#goal-delete").addEventListener("click", async () => {
    if (!window.confirm(`Delete ${goal.name}? Only the goal goes; its history stays in your transactions.`)) return;
    try {
      await api.deleteCategory(goal.id);
      showToast(`${goal.name} is deleted.`);
      navigate("/savings");
    } catch (err) {
      fail(err);
    }
  });
}
