// BlitZave — transactions page.
// Thin shell: a one-line heading, then js/components/transactions-table.js
// does everything else (fetching, filters, sorting, pagination, inline
// metadata editing). A quiet "Adjustments" toggle in the header opens
// js/components/adjustments-panel.js above the table. This page never fetches
// or formats data itself.

import { renderTransactionsTable } from "../components/transactions-table.js";
import { renderAdjustmentsPanel } from "../components/adjustments-panel.js";

const ICON_ADJUSTMENT = `
  <svg class="txns-adjustments-icon" viewBox="0 0 24 24" aria-hidden="true">
    <path d="M4 8h14M14 4l4 4-4 4" />
    <path d="M20 16H6M10 12l-4 4 4 4" />
  </svg>
`;

export function render(root) {
  root.innerHTML = `
    <header class="txns-page-header">
      <div>
        <p class="eyebrow">Ledger</p>
        <h1 class="txns-page-title">Every transaction, one place</h1>
      </div>
      <button type="button" class="btn btn-ghost txns-adjustments-toggle" aria-expanded="false" aria-controls="txns-adjustments">${ICON_ADJUSTMENT}<span>Adjustments</span></button>
    </header>
    <section class="card txns-adjustments" id="txns-adjustments" hidden>
      <p class="txns-adjustments-title">Adjustments</p>
      <div class="txns-adjustments-mount"></div>
    </section>
    <div class="txns-table-mount"></div>
  `;

  let panel = null; // mounted on first open
  const section = root.querySelector(".txns-adjustments");
  const toggle = root.querySelector(".txns-adjustments-toggle");

  const table = renderTransactionsTable(root.querySelector(".txns-table-mount"), {
    onAdjustmentsChanged: () => {
      if (panel) panel.reload();
    },
  });

  toggle.addEventListener("click", () => {
    const open = section.hidden;
    section.hidden = !open;
    toggle.setAttribute("aria-expanded", String(open));
    toggle.classList.toggle("is-active", open);
    if (open && !panel) {
      panel = renderAdjustmentsPanel(root.querySelector(".txns-adjustments-mount"), {
        onUnlinked: () => table.reload(),
      });
    } else if (open) {
      panel.reload();
    }
  });
}
