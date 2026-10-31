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

// { enabled: true|false|null, installed_version, available_version|null }.
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

export function createCategory(name, kind) {
  return request("/api/categories", {
    method: "POST",
    body: JSON.stringify({ name, kind }),
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

export function adjustmentCandidates(transactionId) {
  return request(`/api/transactions/${encodeURIComponent(transactionId)}/transfer-candidates`);
}

export function listAdjustments() {
  return request("/api/transfers");
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

// --------------------------- Refunds / credits ---------------------------

export function refundCandidates(transactionId) {
  return request(`/api/transactions/${encodeURIComponent(transactionId)}/refund-candidates`);
}

export function listRefunds() {
  return request("/api/refunds");
}

export function createRefund(transactionId, counterpartId) {
  return request("/api/refunds", {
    method: "POST",
    body: JSON.stringify({ transaction_id: transactionId, counterpart_id: counterpartId }),
  });
}

export function deleteRefund(id) {
  return request(`/api/refunds/${encodeURIComponent(id)}`, { method: "DELETE" });
}
