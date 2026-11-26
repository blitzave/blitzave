// BlitZave — the Goose drawer, opened from Home. Vibecoded (AGENTS.md "Two
// zones"): every word and figure on a card comes from the engine
// (POST /api/goose, see assistant.py). This file lays the cards out and sends
// back what the user chose. It never formats money and never talks to a model.

// Versioned import: Goose's calls are newer than the api.js an open tab may
// still hold (LESSONS.md 13).
import * as api from "../api.js?v=goose";

const ICON_CLOSE = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>`;

let dialog = null;
let settings = null;

function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function shortDate(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function body() {
  return dialog.querySelector(".goose-body");
}

export async function openGooseDrawer() {
  if (!dialog) {
    dialog = document.createElement("dialog");
    dialog.className = "goose-drawer";
    dialog.setAttribute("aria-labelledby", "goose-title");
    dialog.innerHTML = `
      <header class="goose-head">
        <h2 id="goose-title">Goose</h2>
        <button class="btn btn-quiet" type="button" data-model>Model</button>
        <button class="btn btn-quiet goose-close" type="button" aria-label="Close">${ICON_CLOSE}</button>
      </header>
      <div class="goose-body"></div>
    `;
    dialog.querySelector(".goose-close").addEventListener("click", () => dialog.close());
    dialog.querySelector("[data-model]").addEventListener("click", openSettings);
    // A click on the backdrop lands on the dialog itself.
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    });
    document.body.append(dialog);
    dialog.showModal();
    await start();
  } else {
    dialog.showModal();
    // A model may have been chosen in Settings since this drawer last opened.
    if (!settings?.provider) await start();
  }
  dialog.querySelector(".goose-input")?.focus();
}

async function start() {
  body().innerHTML = `<p class="goose-quiet">One moment…</p>`;
  try {
    settings = await api.gooseSettings();
  } catch (err) {
    body().innerHTML = `<p class="goose-quiet">${esc(err.detail)}</p>`;
    return;
  }
  if (settings.provider) showGoose();
  else showSetUp();
}

// ------------------------------- Asking -------------------------------

function showGoose(question = "") {
  body().innerHTML = `
    <form class="goose-form">
      <input class="input goose-input" name="question" maxlength="500" required autocomplete="off"
             placeholder="Tag May 13–27 as PNW, or: how's my grocery spending?" />
      <button class="btn btn-primary" type="submit">Ask</button>
    </form>
    <div class="goose-answer" aria-live="polite"></div>
  `;
  const form = body().querySelector(".goose-form");
  form.question.value = question;
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    send(form.question.value.trim());
  });
  form.question.focus();
  if (question) send(question);
}

async function send(question) {
  if (!question) return;
  const answer = body().querySelector(".goose-answer");
  const button = body().querySelector(".goose-form button");
  button.disabled = true;
  answer.innerHTML = `<p class="goose-quiet">Working it out…</p>`;
  try {
    const { cards } = await api.ask(question);
    answer.replaceChildren(...cards.map((card) => renderCard(card, question)));
  } catch (err) {
    if (err.status === 409) {
      showSetUp(err.detail);
      return;
    }
    answer.innerHTML = `<section class="card goose-card"><p class="goose-text">${esc(err.detail)}</p></section>`;
  } finally {
    button.disabled = false;
  }
}

function chips(card) {
  const list = (card.chips || []).map((chip) => `<span class="chip">${esc(chip)}</span>`).join("");
  return list ? `<div class="goose-chips">${list}</div>` : "";
}

function rows(card, checkable) {
  const items = card.rows
    .map(
      (row, i) => `
        <li><label class="goose-row">
          ${checkable ? `<input type="checkbox" checked data-row="${i}" />` : ""}
          <span class="goose-row-date">${esc(shortDate(row.posted_on))}</span>
          <span class="goose-row-desc">${esc(row.description)}</span>
          <span class="goose-row-amount u-tabular">${esc(row.amount_display)}</span>
        </label></li>`,
    )
    .join("");
  return items ? `<ul class="goose-rows">${items}</ul>` : "";
}

function renderCard(card, question) {
  const el = document.createElement("section");
  el.className = "card goose-card";

  if (card.type === "summary") {
    const months = card.months
      .map((m) => `<li><span>${esc(m.label)}</span><span class="u-tabular">${esc(m.amount_display)}</span></li>`)
      .join("");
    el.innerHTML = `
      <p class="goose-sentence">${esc(card.text)}</p>
      <p class="goose-figure"><strong class="u-tabular">${esc(card.figure)}</strong><span>${esc(card.figure_label)}</span></p>
      ${chips(card)}
      ${months ? `<details class="goose-months"><summary>Each month</summary><ul>${months}</ul></details>` : ""}
    `;
    return el;
  }

  if (card.type === "clarify") {
    el.innerHTML = `
      <p class="goose-text">${esc(card.text)}</p>
      <div class="goose-actions">${card.options
        .map((o) => `<button class="btn btn-ghost" type="button">${esc(o)}</button>`)
        .join("")}</div>
    `;
    el.querySelectorAll("button").forEach((button, i) =>
      button.addEventListener("click", () => showGoose(`${question} (${card.options[i]})`)),
    );
    return el;
  }

  if (card.type === "proposal") {
    el.innerHTML = `
      <p class="goose-text">${esc(card.text)}</p>
      ${chips(card)}
      ${rows(card, true)}
      <div class="goose-actions">
        <button class="btn btn-primary" type="button" data-apply>Apply</button>
        <button class="btn btn-ghost" type="button" data-cancel>Cancel</button>
      </div>
    `;
    wireProposal(el, card);
    return el;
  }

  // "transactions" and "message": words, chips, and any rows.
  el.innerHTML = `<p class="goose-text">${esc(card.text)}</p>${chips(card)}${card.rows ? rows(card, false) : ""}`;
  return el;
}

function wireProposal(el, card) {
  const actions = el.querySelector(".goose-actions");
  const apply = el.querySelector("[data-apply]");
  const boxes = [...el.querySelectorAll("[data-row]")];
  boxes.forEach((box) =>
    box.addEventListener("change", () => {
      apply.disabled = !boxes.some((b) => b.checked);
    }),
  );
  // Cancel writes nothing; the preview just closes on the engine's side later.
  el.querySelector("[data-cancel]").addEventListener("click", () => el.remove());

  apply.addEventListener("click", async () => {
    const chosen = boxes.filter((b) => b.checked).map((b) => Number(b.dataset.row));
    apply.disabled = true;
    try {
      const result = await api.applyProposal(card.proposal_id, chosen);
      boxes.forEach((b) => (b.disabled = true));
      actions.innerHTML = `
        <p class="goose-text">${esc(result.text)}</p>
        <button class="btn btn-ghost" type="button" data-undo>Undo</button>
      `;
      actions.querySelector("[data-undo]").addEventListener("click", async (event) => {
        event.target.disabled = true;
        try {
          actions.innerHTML = `<p class="goose-text">${esc((await api.undoProposal(card.proposal_id)).text)}</p>`;
        } catch (err) {
          actions.innerHTML = `<p class="goose-text">${esc(err.detail)}</p>`;
        }
      });
    } catch (err) {
      actions.innerHTML = `<p class="goose-text">${esc(err.detail)}</p>`;
    }
  });
}

// ------------------------------ The model ------------------------------
// Chosen in Settings (#/settings), the one place for it: Goose needs a model
// before its first question, and asking without one lands here too.

function showSetUp(note = "") {
  body().innerHTML = `
    <div class="goose-setup">
      <p class="goose-quiet">${esc(note || "Goose needs a model first: Ollama on this laptop, or Claude.")}</p>
      <a class="btn btn-primary" href="#/settings" data-settings>Choose one in Settings</a>
    </div>
  `;
  body().querySelector("[data-settings]").addEventListener("click", () => dialog.close());
}

function openSettings() {
  dialog.close();
  window.location.hash = "/settings";
}
