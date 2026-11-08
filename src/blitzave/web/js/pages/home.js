// BlitZave — Home (#/home, the landing page). The design canvas's chosen
// "ring": one sentence, one number, calm on top. The ring is how far through
// the month it is. Inside it, the one thing worth knowing today; below it,
// one supporting sentence and three figures that open the detail one tap down.
//
// The engine picks the message (refine/home.py) and writes it (copy/home.py).
// This page only lays it out: every figure is a *_display string, and the
// ring's geometry comes from the day of the month.

// Versioned import: a tab left open across an update still holds the old
// api.js, which lacks the goal and export calls (LESSONS.md 13).
import * as api from "../api.js?v=goals";

const R = 190;
const C = 2 * Math.PI * R;

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export async function render(root) {
  root.innerHTML = `
    <div class="home-page">
      <div class="home-ring home-ring--loading" aria-hidden="true">
        <div class="skeleton" style="width: 60%; height: 1.4em;"></div>
      </div>
    </div>
  `;
  let home;
  try {
    home = await api.home();
  } catch (err) {
    root.innerHTML = `
      <div class="empty-state">
        <p class="empty-state-title">Home didn't load</p>
        <p class="empty-state-body">${escapeHtml((err && err.detail) || "Something went wrong reaching BlitZave.")}</p>
        <button class="btn btn-ghost" type="button" id="home-retry">Try again</button>
      </div>
    `;
    root.querySelector("#home-retry").addEventListener("click", () => render(root));
    return;
  }

  const through = home.day / home.days_in_month;
  const arc = (through * C).toFixed(1);
  // Where the arc ends: a small marker for today.
  const angle = through * 2 * Math.PI - Math.PI / 2;
  const dotX = (200 + R * Math.cos(angle)).toFixed(1);
  const dotY = (200 + R * Math.sin(angle)).toFixed(1);
  const goals = home.goals_underway;

  root.innerHTML = `
    <section class="home-page" aria-labelledby="home-today">
      <div class="home-ring">
        <svg class="home-ring-art" viewBox="0 0 400 400" aria-hidden="true">
          <circle class="home-ring-track" cx="200" cy="200" r="${R}" />
          <circle class="home-ring-month" cx="200" cy="200" r="${R}"
                  stroke-dasharray="${arc} ${C.toFixed(1)}" transform="rotate(-90 200 200)" />
          <circle class="home-ring-today" cx="${dotX}" cy="${dotY}" r="6" />
          <circle class="home-ring-face" cx="200" cy="200" r="150" />
        </svg>
        <div class="home-ring-text">
          <span class="home-ring-date">${escapeHtml(home.month_label)}, day ${home.day}</span>
          <h1 class="home-headline" id="home-today">${escapeHtml(home.headline)}</h1>
        </div>
      </div>
      <p class="home-support">${escapeHtml(home.support)}</p>
      <nav class="home-snapshots" aria-label="This month">
        <a class="home-snapshot" href="#/transactions"><strong class="u-tabular">${escapeHtml(home.spent_display)}</strong><span>spent so far</span></a>
        <a class="home-snapshot" href="#/savings"><strong class="u-tabular">${escapeHtml(home.set_aside_display)}</strong><span>set aside</span></a>
        <a class="home-snapshot" href="#/savings"><strong class="u-tabular">${goals}</strong><span>${goals === 1 ? "goal underway" : "goals underway"}</span></a>
      </nav>
      <p class="home-footnote">Everything here stays on this laptop.</p>
    </section>
  `;
}
