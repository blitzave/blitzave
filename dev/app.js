// Throwaway dev tool — see AGENTS.md ("Exception: engine/dev/"). No build step on purpose.

async function callApi(method, path, body) {
  const response = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await response.text();
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch {
    parsed = text;
  }
  return { ok: response.ok, status: response.status, body: parsed };
}

function show(el, result) {
  el.textContent = `${result.status}\n${JSON.stringify(result.body, null, 2)}`;
}

function setBadge(connected) {
  const badge = document.getElementById("connect-badge");
  badge.classList.remove("unknown", "connected", "disconnected");
  if (connected === null) {
    badge.textContent = "unknown";
    badge.classList.add("unknown");
    return;
  }
  badge.textContent = connected ? "connected" : "disconnected";
  badge.classList.add(connected ? "connected" : "disconnected");
}

async function refreshConnectStatus() {
  try {
    const result = await callApi("GET", "/api/connect");
    setBadge(result.ok ? result.body.connected : null);
  } catch {
    setBadge(null);
  }
}

document.getElementById("health-check").addEventListener("click", async () => {
  const el = document.getElementById("health-result");
  el.textContent = "…";
  show(el, await callApi("GET", "/api/health"));
});

document.getElementById("connect-refresh").addEventListener("click", refreshConnectStatus);

document.getElementById("connect-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const el = document.getElementById("connect-result");
  const setupToken = document.getElementById("setup-token").value.trim();
  el.textContent = "…";
  const result = await callApi("POST", "/api/connect", { setup_token: setupToken });
  show(el, result);
  await refreshConnectStatus();
});

document.getElementById("sync-trigger").addEventListener("click", async () => {
  const el = document.getElementById("sync-result");
  el.textContent = "…";
  show(el, await callApi("POST", "/api/sync"));
  await refreshConnectStatus();
});

document.getElementById("accounts-load").addEventListener("click", async () => {
  const el = document.getElementById("accounts-result");
  el.textContent = "…";
  show(el, await callApi("GET", "/api/accounts"));
});

document.getElementById("account-correction-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const el = document.getElementById("account-correction-result");
  const accountId = document.getElementById("account-id").value;
  const accountType = document.getElementById("account-type").value;
  const subtype = document.getElementById("account-subtype").value.trim();

  const body = { account_type: accountType };
  if (accountType === "asset") {
    body.asset_type = subtype;
  } else {
    body.liability_type = subtype;
  }

  el.textContent = "…";
  show(el, await callApi("PATCH", `/api/accounts/${accountId}`, body));
});

refreshConnectStatus();
