// BlitZave — hash router.
//
// Tiny by design: a route table, a matcher that supports one kind of
// dynamic segment (:id), and a render call into whatever root element
// main.js hands it. No history API — hash routing is enough for a
// single-page localhost app and needs no server-side route handling.

const routes = [
  { pattern: "/onboarding", load: () => import("./pages/onboarding.js") },
  { pattern: "/accounts/:id", load: () => import("./pages/account-detail.js") },
  { pattern: "/accounts", load: () => import("./pages/accounts.js") },
  { pattern: "/transactions", load: () => import("./pages/transactions.js") },
  { pattern: "/categories", load: () => import("./pages/categories.js") },
];

const DEFAULT_PATH = "/accounts";

let rootEl = null;
let renderToken = 0;

function currentPath() {
  const raw = window.location.hash.slice(1) || "/";
  return raw.startsWith("/") ? raw : `/${raw}`;
}

function normalize(path) {
  return path === "/" ? DEFAULT_PATH : path;
}

function matchRoute(path) {
  const pathSegs = path.split("/").filter(Boolean);
  for (const route of routes) {
    const patSegs = route.pattern.split("/").filter(Boolean);
    if (patSegs.length !== pathSegs.length) continue;

    const params = {};
    let matched = true;
    for (let i = 0; i < patSegs.length; i++) {
      if (patSegs[i].startsWith(":")) {
        params[patSegs[i].slice(1)] = decodeURIComponent(pathSegs[i]);
      } else if (patSegs[i] !== pathSegs[i]) {
        matched = false;
        break;
      }
    }
    if (matched) return { route, params };
  }
  return null;
}

// A nav link is active if its href is the current section, or an ancestor
// of it (e.g. the "Accounts" link stays lit on #/accounts/ACT-123).
function highlightActiveLink(path) {
  document.querySelectorAll("[data-nav-link]").forEach((link) => {
    const href = link.getAttribute("href") || "";
    const linkPath = href.startsWith("#") ? href.slice(1) : href;
    const isActive = path === linkPath || path.startsWith(`${linkPath}/`);
    link.classList.toggle("is-active", isActive);
  });
}

async function renderCurrentRoute() {
  if (!rootEl) return;

  const token = ++renderToken;
  const path = normalize(currentPath());
  const matched = matchRoute(path);

  if (!matched) {
    window.location.replace(`#${DEFAULT_PATH}`);
    return;
  }

  highlightActiveLink(path);

  try {
    const mod = await matched.route.load();
    if (token !== renderToken) return; // a newer navigation started while this one loaded
    rootEl.innerHTML = "";
    mod.render(rootEl, matched.params);
  } catch (err) {
    if (token !== renderToken) return;
    console.error("BlitZave: page failed to load", err);
    rootEl.innerHTML =
      '<div class="empty-state"><p class="empty-state-title">This page hit a snag</p>' +
      '<p class="empty-state-body">Reloading usually fixes it. If it keeps happening, something deeper broke.</p></div>';
  }
}

/** Start routing into `root`. Renders the current hash immediately, then
 * re-renders on every hashchange. */
export function startRouter(root) {
  rootEl = root;
  window.addEventListener("hashchange", renderCurrentRoute);
  renderCurrentRoute();
}

/** Navigate to a path like "/accounts" or "/accounts/ACT-123". */
export function navigate(path) {
  window.location.hash = path.startsWith("/") ? path : `/${path}`;
}
