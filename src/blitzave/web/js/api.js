// BlitZave — API client.
//
// Every function here maps 1:1 to one endpoint in the backend contract
// (see CLAUDE.md). This is the only file that knows the URLs. Pages never
// call fetch() directly.
//
// Money rule: this file never computes or formats money. It only ever
// passes through whatever *_display strings the API already sent.

/**
 * @typedef {Error & {status: number, detail: string}} ApiError
 */

async function request(path, options = {}) {
  let res;
  try {
    res = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
  } catch {
    // The server is unreachable (not running, network down). Shaped the
    // same as an HTTP error so callers only ever check one thing.
    const err = new Error("Couldn't reach BlitZave. Is it running?");
    err.status = 0;
    err.detail = "Couldn't reach BlitZave. Is it running?";
    throw err;
  }

  if (res.status === 204) return null;

  const text = await res.text();
  let body = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
  }

  if (!res.ok) {
    const detail = (body && body.detail) || res.statusText || "Something went wrong.";
    const err = new Error(detail);
    err.status = res.status;
    err.detail = detail;
    throw err;
  }

  return body;
}

function query(params = {}) {
  const parts = Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== null && v !== "")
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`);
  return parts.length ? `?${parts.join("&")}` : "";
}

// ------------------------------- Health -------------------------------

export function health() {
  return request("/api/health");
}

// ------------------------------- Connect -------------------------------

export function connectStatus() {
  return request("/api/connect");
}

export function connect(setupToken) {
  return request("/api/connect", {
    method: "POST",
    body: JSON.stringify({ setup_token: setupToken }),
  });
}

// -------------------------------- Sync --------------------------------

export function sync() {
  return request("/api/sync", { method: "POST" });
}

// month: "YYYY-MM". Re-fetches that calendar month from the bank.
export function syncMonth(month) {
  return request("/api/sync/month", {
    method: "POST",
    body: JSON.stringify({ month }),
  });
}

// ------------------------------- Updates -------------------------------

// { enabled: true|false|null, installed_version, available_version|null,
//   release_url|null } (release_url: the notes page for available_version).
// Reads what the engine stored; this call never reaches the internet.
export function updateStatus() {
  return request("/api/updates");
}

export function setUpdateCheck(enabled) {
  return request("/api/updates", {
    method: "PUT",
    body: JSON.stringify({ enabled }),
  });
}

// ------------------------------ Accounts ------------------------------

export function listAccounts() {
  return request("/api/accounts");
}

export function patchAccount(id, body) {
  return request(`/api/accounts/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

// ----------------------------- Categories -----------------------------

export function listCategories() {
  return request("/api/categories");
}

// extra: the rest of a category, e.g. a goal's budget_amount, schedule,
// target_on / period_months / ends_on, goal_level and account_id.
export function createCategory(name, kind, extra = {}) {
  return request("/api/categories", {
    method: "POST",
    body: JSON.stringify({ name, kind, ...extra }),
  });
}

export function updateCategory(id, body) {
  return request(`/api/categories/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function deleteCategory(id) {
  return request(`/api/categories/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export function categorySummary(start, end) {
  return request(`/api/categories/summary${query({ start, end })}`);
}

// -------------------------------- Goals --------------------------------
// A goal is a savings category that lives in a goal account (asset_type
// "targeted"). Goals are created and edited through /api/categories; these
// two read and reorder them.

// { total_display, accounts: [goal accounts, each with goals in line,
//   saved/to-go/budget displays and a state], completed: [...] }
export function listGoals() {
  return request("/api/goals");
}

// goalIds: every active goal in the account, top first. Returns the account
// with its new split, the same shape as one entry of listGoals().accounts.
export function reorderGoals(accountId, goalIds) {
  return request(`/api/accounts/${encodeURIComponent(accountId)}/goal-order`, {
    method: "PUT",
    body: JSON.stringify({ goal_ids: goalIds }),
  });
}

// "Was that the PS5?": answer one question from listGoals().questions.
// answer: "bought" (completes the goal) or "other". Returns the whole of
// listGoals() so the page can redraw.
export function answerGoalQuestion(transactionId, goalId, answer) {
  return request(`/api/goals/questions/${encodeURIComponent(transactionId)}`, {
    method: "POST",
    body: JSON.stringify({ goal_id: goalId, answer }),
  });
}

// -------------------------------- Tags --------------------------------

export function listTags() {
  return request("/api/tags");
}

export function createTag(name) {
  return request("/api/tags", {
    method: "POST",
    body: JSON.stringify({ name }),
  });
}

export function deleteTag(id) {
  return request(`/api/tags/${encodeURIComponent(id)}`, { method: "DELETE" });
}

// ---------------------------- Transactions ----------------------------

export function listTransactions(params = {}) {
  return request(`/api/transactions${query(params)}`);
}

export function patchTransaction(id, body) {
  return request(`/api/transactions/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

// ----------------------------- Adjustments -----------------------------
// A pair of transactions that cancel out — a refund, reversal, payment, or
// money moved between accounts — linked so neither counts as spending or income.
// Every pair is one /api/transfers row; its `kind` (transfer, refund, credit)
// is worked out by the server.

export function adjustmentCandidates(transactionId) {
  return request(`/api/transactions/${encodeURIComponent(transactionId)}/transfer-candidates`);
}

export function listAdjustments(kind = null) {
  return request(kind ? `/api/transfers?kind=${encodeURIComponent(kind)}` : "/api/transfers");
}

export function getAdjustment(id) {
  return request(`/api/transfers/${encodeURIComponent(id)}`);
}

export function createAdjustment(transactionId, counterpartId) {
  return request("/api/transfers", {
    method: "POST",
    body: JSON.stringify({ transaction_id: transactionId, counterpart_id: counterpartId }),
  });
}

export function deleteAdjustment(id) {
  return request(`/api/transfers/${encodeURIComponent(id)}`, { method: "DELETE" });
}
