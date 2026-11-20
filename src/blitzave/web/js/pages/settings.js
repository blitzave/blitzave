// BlitZave — Settings (#/settings), one tap down from the profile row above
// the sidebar footer. Two things for now: which profile is showing (Live,
// your own money, or Demo, a made-up household that starts over each time
// it opens), and the model Goose uses, which both profiles share.
//
// Vibecoded (AGENTS.md "Two zones"): the engine decides what's allowed
// (PUT /api/profile refuses Live until a bank is connected; PUT
// /api/goose/settings refuses a remote model without the user's OK) and
// writes the disclosure. This page lays it out and sends back the choices.

// Versioned import: profiles and the model list are newer than the api.js an
// open tab may still hold (LESSONS.md 13).
import * as api from "../api.js?v=profiles";

const OTHER = "__other";
const OLLAMA_URL = "http://127.0.0.1:11434";
const LOOPBACK = ["127.0.0.1", "localhost", "[::1]"];

function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export async function render(root) {
  root.innerHTML = `
    <div class="settings-page">
      <header class="settings-intro">
        <p class="eyebrow">This laptop</p>
        <h1 class="settings-intro-title">Settings</h1>
        <p class="settings-intro-lede">Which profile you're in, and the model Goose uses.</p>
      </header>
      <section class="card settings-card" data-profile aria-labelledby="settings-profile-title">
        <p class="settings-quiet">One moment…</p>
      </section>
      <section class="card settings-card" data-goose aria-labelledby="settings-goose-title">
        <p class="settings-quiet">One moment…</p>
      </section>
    </div>
  `;
  renderProfile(root.querySelector("[data-profile]"));
  renderGoose(root.querySelector("[data-goose]"));
}

// ------------------------------- Profile -------------------------------

async function renderProfile(card) {
  let profile;
  let connect;
  try {
    [profile, connect] = await Promise.all([api.profile(), api.connectStatus()]);
  } catch (err) {
    card.innerHTML = `<p class="field-error">${esc(err.detail)}</p>`;
    return;
  }
  const demo = profile.name === "demo";
  let hint;
  if (!connect.can_connect) {
    hint = "This is a throwaway demo, deleted when you stop it. To see your own money, stop it and run <code>blitzave</code>.";
  } else if (!profile.live_available) {
    hint = "A made-up household that starts over each time you open it, so try anything. Live opens once you connect your bank.";
  } else if (demo) {
    hint = "You're in Demo. It starts over each time you open it, and there's nothing in it to sync. Live is untouched.";
  } else {
    hint = "Your own accounts, synced from SimpleFIN. Demo is still here, fresh each time, whenever you want to try something without your own numbers.";
  }
  card.innerHTML = `
    <h2 class="settings-card-title" id="settings-profile-title">Profile</h2>
    <div class="settings-switch" role="radiogroup" aria-labelledby="settings-profile-title">
      <button type="button" role="radio" data-name="demo" aria-checked="${demo}">Demo</button>
      <button type="button" role="radio" data-name="live" aria-checked="${!demo}"
        ${profile.live_available ? "" : 'disabled title="Connect your bank first"'}>Live</button>
    </div>
    <p class="settings-hint">${hint}</p>
    ${connect.can_connect && !profile.live_available
      ? `<div><button type="button" class="btn btn-ghost" data-connect>Connect your bank</button></div>`
      : ""}
    <p class="field-error" data-error hidden></p>
  `;
  card.querySelector("[data-connect]")?.addEventListener("click", () => window.__bz.startOnboarding());
  card.querySelectorAll("[data-name]").forEach((button) => {
    button.addEventListener("click", () => choose(card, button.dataset.name, profile.name));
  });
}

async function choose(card, name, showing) {
  if (name === showing) return;
  const error = card.querySelector("[data-error]");
  card.querySelectorAll("[data-name]").forEach((button) => (button.disabled = true));
  card.querySelector(".settings-hint").textContent =
    name === "demo" ? "Setting up a fresh Demo…" : "Opening Live…";
  try {
    await api.chooseProfile(name);
  } catch (err) {
    error.textContent = err.detail;
    error.hidden = false;
    renderProfile(card);
    return;
  }
  // Every page's data just changed: start again from here, the new profile showing.
  window.location.reload();
}

// ------------------------------ Goose's model ------------------------------

async function renderGoose(card) {
  let saved;
  try {
    saved = await api.gooseSettings();
  } catch (err) {
    card.innerHTML = `<p class="field-error">${esc(err.detail)}</p>`;
    return;
  }
  const state = {
    saved,
    provider: saved.provider || "ollama",
    model: saved.model || "",
    baseUrl: saved.provider === "ollama" && saved.base_url ? saved.base_url : OLLAMA_URL,
    names: [],
    other: false,
    keyEditing: !saved.key_saved,
    // The OK was given with the settings it was saved for (assistant.save_settings).
    remoteOk: Boolean(saved.remote),
  };
  card.innerHTML = `
    <h2 class="settings-card-title" id="settings-goose-title">Goose's model</h2>
    <p class="settings-hint">Goose turns your question into steps BlitZave runs on this laptop.
      The model plans those steps; it never sees your transactions.</p>
    <div class="settings-goose" data-form></div>
  `;
  await loadNames(state);
  drawGoose(card, state);
}

async function loadNames(state) {
  try {
    const listed = await api.gooseModels(state.provider, state.provider === "ollama" ? state.baseUrl : null);
    state.names = listed.models;
  } catch {
    state.names = [];
  }
  if (!state.model && state.names.length) state.model = state.names[0];
  state.other = Boolean(state.model) && !state.names.includes(state.model);
}

function isRemote(state) {
  if (state.provider === "anthropic") return true;
  try {
    return !LOOPBACK.includes(new URL(state.baseUrl).hostname);
  } catch {
    return false; // the engine checks the address itself
  }
}

function drawGoose(card, state) {
  const form = card.querySelector("[data-form]");
  const claude = state.provider === "anthropic";
  const remote = isRemote(state);
  const locked = state.saved.key_saved && !state.keyEditing;
  const showOther = state.other || !state.names.length;
  const complete = Boolean(state.model) && (!remote || state.remoteOk) && (!claude || state.saved.key_saved);
  const ready = complete && isSaved(state);

  form.innerHTML = `
    <div class="settings-row">
      <label class="field"><span class="field-label">Provider</span>
        <select class="select" data-provider>
          <option value="ollama" ${!claude ? "selected" : ""}>Ollama, on this laptop</option>
          <option value="anthropic" ${claude ? "selected" : ""}>Claude, from Anthropic</option>
        </select>
      </label>
      ${state.names.length
        ? `<label class="field"><span class="field-label">Model</span>
            <select class="select" data-model-pick>
              ${state.names.map((name) => `<option value="${esc(name)}" ${!showOther && state.model === name ? "selected" : ""}>${esc(name)}</option>`).join("")}
              <option value="${OTHER}" ${showOther ? "selected" : ""}>Other…</option>
            </select>
          </label>`
        : ""}
    </div>
    ${showOther
      ? `<label class="field"><span class="field-label">Model name</span>
          <input class="input" data-model-name autocomplete="off" value="${esc(state.other ? state.model : "")}"
            placeholder="${claude ? "claude-…" : "the name Ollama lists for it, like qwen3:8b"}" />
          ${!claude && !state.names.length
            ? `<span class="field-hint">Ollama didn't answer at this address, so type the name it lists.</span>`
            : ""}
        </label>`
      : ""}
    ${!claude
      ? `<label class="field"><span class="field-label">Ollama's address</span>
          <input class="input" data-base-url autocomplete="off" value="${esc(state.baseUrl)}" />
        </label>`
      : ""}
    ${claude
      ? `<div class="field"><span class="field-label" id="settings-key-label">API key</span>
          <div class="settings-key-row">
            <input class="input" type="password" data-key autocomplete="off" aria-labelledby="settings-key-label"
              ${locked ? "disabled" : ""} placeholder="${locked ? "Saved in your keychain" : "sk-ant-…"}" />
            ${locked
              ? `<button type="button" class="btn btn-ghost" data-key-reset>Reset</button>`
              : `<button type="button" class="btn btn-primary" data-key-save disabled>Save</button>
                 ${state.saved.key_saved ? `<button type="button" class="btn btn-quiet" data-key-cancel>Cancel</button>` : ""}`}
          </div>
          <span class="field-hint">${locked
            ? "Reset opens the box again. The saved key stays until you save a new one."
            : state.remoteOk ? "It goes to your system's keychain, never the database." : "Tick the box below first."}</span>
        </div>`
      : ""}
    ${remote
      ? `<div class="settings-disclosure">
          <p>${esc(state.saved.disclosure)}</p>
          <label><input type="checkbox" data-remote-ok ${state.remoteOk ? "checked" : ""} /> That's fine, send those</label>
        </div>`
      : `<p class="settings-local">Runs on this laptop. Nothing leaves it.</p>`}
    <div class="settings-status-row">
      <p class="settings-status"><i class="settings-status-dot ${ready ? "is-ready" : ""}"></i>${esc(statusLine(state, ready, claude, remote))}</p>
      ${complete && !ready ? `<button type="button" class="btn btn-primary" data-use>Use ${esc(state.model)}</button>` : ""}
    </div>
    <p class="field-error" data-error hidden></p>
  `;
  wireGoose(card, state);
}

// What's on screen is what the engine has saved.
function isSaved(state) {
  const s = state.saved;
  return (
    s.provider === state.provider &&
    s.model === state.model &&
    (state.provider === "anthropic" || s.base_url === state.baseUrl.replace(/\/+$/, ""))
  );
}

function statusLine(state, ready, claude, remote) {
  if (!state.model) return "Choose a model for Goose.";
  if (claude && !state.saved.key_saved) return "Goose is ready once a key is saved.";
  if (remote && !state.remoteOk) return "Goose is ready once you've OK'd what it sends.";
  if (!ready) return state.saved.provider ? "Not saved yet." : "Goose doesn't have a model yet.";
  return claude
    ? `Goose uses ${state.model}, with the key in your keychain.`
    : `Goose uses ${state.model}${remote ? ", on that machine" : ", on this laptop"}.`;
}

function wireGoose(card, state) {
  const form = card.querySelector("[data-form]");
  const redraw = () => drawGoose(card, state);

  form.querySelector("[data-provider]").addEventListener("change", async (event) => {
    state.provider = event.target.value;
    state.model = state.saved.provider === state.provider ? state.saved.model : "";
    state.remoteOk = state.saved.provider === state.provider && Boolean(state.saved.remote);
    await loadNames(state);
    redraw();
    save(card, state);
  });

  form.querySelector("[data-model-pick]")?.addEventListener("change", (event) => {
    if (event.target.value === OTHER) {
      state.other = true;
      state.model = "";
      redraw();
      form.querySelector("[data-model-name]")?.focus();
      return;
    }
    state.other = false;
    state.model = event.target.value;
    save(card, state);
  });

  form.querySelector("[data-model-name]")?.addEventListener("change", (event) => {
    state.other = true;
    state.model = event.target.value.trim();
    save(card, state);
  });

  form.querySelector("[data-base-url]")?.addEventListener("change", async (event) => {
    state.baseUrl = event.target.value.trim() || OLLAMA_URL;
    state.remoteOk = false; // a new address asks again
    await loadNames(state);
    redraw();
    save(card, state);
  });

  form.querySelector("[data-use]")?.addEventListener("click", () => save(card, state));

  form.querySelector("[data-remote-ok]")?.addEventListener("change", (event) => {
    state.remoteOk = event.target.checked;
    redraw();
    save(card, state);
  });

  const key = form.querySelector("[data-key]");
  const keySave = form.querySelector("[data-key-save]");
  key?.addEventListener("input", () => {
    if (keySave) keySave.disabled = !(key.value.trim() && state.remoteOk);
  });
  keySave?.addEventListener("click", () => save(card, state, key.value.trim()));
  form.querySelector("[data-key-reset]")?.addEventListener("click", () => {
    state.keyEditing = true;
    redraw();
    form.querySelector("[data-key]")?.focus();
  });
  form.querySelector("[data-key-cancel]")?.addEventListener("click", () => {
    state.keyEditing = false;
    redraw();
  });
}

// Saves as soon as the choice is complete: a model, the OK for a remote one,
// and for Claude a key. The engine checks all of it again.
async function save(card, state, apiKey = null) {
  const claude = state.provider === "anthropic";
  const remote = isRemote(state);
  const complete = state.model && (!remote || state.remoteOk) && (!claude || state.saved.key_saved || apiKey);
  if (!complete) {
    drawGoose(card, state);
    return;
  }
  const error = card.querySelector("[data-error]");
  try {
    state.saved = await api.saveGooseSettings({
      provider: state.provider,
      model: state.model,
      base_url: claude ? null : state.baseUrl,
      api_key: apiKey,
      remote_ok: state.remoteOk,
    });
  } catch (err) {
    error.textContent = err.detail;
    error.hidden = false;
    return;
  }
  if (apiKey) state.keyEditing = false;
  drawGoose(card, state);
  const status = card.querySelector(".settings-status");
  status.append(" Saved.");
}
