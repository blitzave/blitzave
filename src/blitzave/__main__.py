import argparse
import atexit
import logging
import os
import shutil
import signal
import sys
import webbrowser
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

import keyring
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from blitzave import demo, jobs, keychain
from blitzave.api.routes import router
from blitzave.store import db, users

logger = logging.getLogger(__name__)

# dev/ — a throwaway debug page, not the product UI. See AGENTS.md
# ("Two zones"). Mounted same-origin so it needs no CORS.
DEV_DIR = Path(__file__).resolve().parents[2] / "dev"

# blitzave/web/ — the vibecoded UI, served same-origin at / so the localhost
# API never needs CORS. See AGENTS.md ("The vibecoded zone"). It lives inside
# the package so `uv tool install` ships it; dev/ above is left out on purpose.
WEB_DIR = Path(__file__).resolve().parent / "web"

HOST = "127.0.0.1"
PORT = 8742
URL = f"http://{HOST}:{PORT}"

# DNS rebinding: a page on evil.example can point its own name at 127.0.0.1,
# and the browser then lets it read this server as if it were its own site.
# The request's Host header still says evil.example, so any name but ours
# gets a 400. Only the name is checked: a page can't make the browser send
# 127.0.0.1 or localhost unless it really was opened there.
ALLOWED_HOSTS = [HOST, "localhost"]

# Methods that only read. Everything else is a write, and gets checked below.
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# FastAPI's API docs load their script from a CDN, so they're left out of
# the page rules below (which would block it).
DOCS_PATHS = {"/docs", "/docs/oauth2-redirect", "/redoc"}

# Sent with every page and answer. The page runs only its own files, no
# other site may show it inside a frame (where it could be clicked by
# trickery), and the sites it links to aren't told where it was opened.
# Inline styles stay allowed: the UI writes style="" attributes in markup.
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; object-src 'none'; "
        "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


async def same_origin_writes(request: Request, call_next):
    """Cross-site writes: any website the user visits can make their browser
    send a request here. It can't read the answer (there's no CORS), but a
    write would still land. A browser always says which page a write came
    from, in Origin, so a write from any page but this one gets a 403. A
    request with no Origin didn't come from a web page (curl, the tests)."""
    origin = request.headers.get("origin")
    ours = f"http://{request.headers.get('host')}"
    if request.method not in SAFE_METHODS and origin is not None and origin != ours:
        return PlainTextResponse("Cross-origin request refused", status_code=403)
    response = await call_next(request)
    if request.url.path not in DOCS_PATHS:
        response.headers.update(SECURITY_HEADERS)
    return response


class RevalidatingStaticFiles(StaticFiles):
    """The UI is plain ES modules with no build step and no hashed filenames,
    so after an update the browser must re-check every file or it can run a
    mix of old and new modules. no-cache still allows cached copies; it only
    forces a revalidation, which on localhost is an instant 304."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    conn = db.connect()
    try:
        users.get_or_create_user(conn)
    finally:
        conn.close()

    # "The ally shows up when you open it" — sync on startup so there's
    # something to see with no button to press. A failed/skipped sync must
    # never take the API down (AGENTS.md: "the API reports an honest 'last
    # synced'"), so this only logs.
    try:
        jobs.sync()
    except jobs.SyncError as e:
        logger.info("startup sync skipped: %s", e)

    # Opt-in and at most once a day (see jobs.check_for_update). Offline or
    # no release yet just means there's nothing new to show. Never in demo
    # mode: a demo touches the network for nothing.
    if jobs.DEMO is None:
        try:
            jobs.check_for_update(date.today())
        except jobs.UpdateCheckError as e:
            logger.info("update check skipped: %s", e)

    yield


def create_app() -> FastAPI:
    app = FastAPI(title="BlitZave", lifespan=lifespan)
    app.middleware("http")(same_origin_writes)
    # Added last, so it runs first and wraps everything below: the API,
    # /docs, /dev and the UI.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)
    app.include_router(router)
    if DEV_DIR.is_dir():
        app.mount("/dev", RevalidatingStaticFiles(directory=DEV_DIR, html=True), name="dev")
    if WEB_DIR.is_dir():
        # Mounted last: API routes and /docs are matched first, everything
        # else falls through to the UI's static files.
        app.mount("/", RevalidatingStaticFiles(directory=WEB_DIR, html=True), name="web")
    return app


app = create_app()


class BrowserOpeningServer(uvicorn.Server):
    """Opens the UI in a browser tab once the server is listening. uvicorn
    runs the lifespan (and so the startup sync) before it binds the socket,
    so opening from the lifespan could beat the server to it. A failed start
    exits inside startup(), so a tab only opens on a server that's up."""

    async def startup(self, sockets=None):
        await super().startup(sockets)
        webbrowser.open(f"http://{HOST}:{self.config.port}")


def _fresh_install() -> bool:
    """No database yet and nothing in the keychain: someone who has never
    connected a bank. Anyone with real data never sees made-up numbers, even
    if their keychain entry went missing."""
    return not db.database_exists() and not jobs.simplefin_connected()


def _start_demo(can_connect: bool) -> None:
    """Made-up data in a temporary folder, removed however the process ends:
    Ctrl+C arrives as a KeyboardInterrupt and kill as SIGTERM, which uvicorn
    raises again after shutting down. Both end in atexit. can_connect: the
    keychain is read before the swap, so connecting can go back to it."""
    started = jobs.start_demo(keyring.get_keyring() if can_connect else None)
    atexit.register(shutil.rmtree, started.home, ignore_errors=True)
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(0))
    demo.set_up(app)
    then = "Connect your bank in the app" if can_connect else "Stop it (Ctrl+C) and run `blitzave`"
    print(
        "BlitZave is showing a made-up household. Nothing here is yours, and it's "
        f"deleted when you stop. {then} when you're ready.",
        file=sys.stderr,
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="blitzave", description="BlitZave, on your own machine.")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="try it on a made-up household beside your real one; nothing is kept, "
        "and your real setup is never touched",
    )
    args = parser.parse_args()
    # Every file BlitZave makes from here on (the database, its backups, a dev
    # trace) is readable by this user alone, not only the folder it sits in.
    os.umask(0o077)
    if not args.demo:
        # Before anything reads a secret (the fresh-install check below does):
        # the OS keychain, or a file only the user can read when this machine
        # has no keychain (keychain.py). Not for --demo, which has its own
        # keychain and must never touch the real home folder.
        keychain.use_file_if_needed(db.home_dir())
    try:
        port = PORT
        if args.demo:
            _start_demo(can_connect=False)
            port = demo.PORT
        elif _fresh_install():
            _start_demo(can_connect=True)
        BrowserOpeningServer(uvicorn.Config(app, host=HOST, port=port)).run()
    except KeyboardInterrupt:
        pass  # Ctrl+C: uvicorn has already shut down; this hides the traceback.


if __name__ == "__main__":
    main()
