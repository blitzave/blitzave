// BlitZave — transactions page.
// Thin shell: a one-line heading, then js/components/transactions-table.js
// does everything else (fetching, filters, sorting, pagination, inline
// metadata editing). This page never fetches or formats data itself.

import { renderTransactionsTable } from "../components/transactions-table.js";

export function render(root) {
  root.innerHTML = `
    <header class="txns-page-header">
      <h1 class="txns-page-title">Every transaction, one place</h1>
    </header>
    <div class="txns-table-mount"></div>
  `;
  renderTransactionsTable(root.querySelector(".txns-table-mount"), {});
}
