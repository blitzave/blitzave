# Security policy

BlitZave holds people's financial history, so we treat security reports as
the highest priority.

## Reporting a vulnerability

**Please don't open a public issue.** Report privately through GitHub:
**Security → Advisories → Report a vulnerability** on
[blitzave/blitzave](https://github.com/blitzave/blitzave/security/advisories/new).

Include what you found, how to reproduce it, and the impact you expect. Never
include real financial data or credentials, yours or anyone else's. We'll
acknowledge your report within a few days and keep you updated until it's
resolved. We're glad to credit you in the release notes.

## What's in scope

BlitZave is a single-user app that listens only on `127.0.0.1`. The issues we
care most about:

- Anything that lets a website, another local user, or another process read or
  change BlitZave data (for example cross-origin requests to the local API, or
  DNS rebinding).
- The SimpleFIN access credential leaking out of the OS keychain into the
  database, logs, exports, or error messages.
- Data leaving the machine other than the user's own SimpleFIN sync and the
  opt-in update check (a single request for the latest release, nothing sent).
- Paths outside `src/blitzave/web/` being served by the static file server.
- Money integrity bugs: amounts that are rounded, duplicated, or changed
  silently.

## Supported versions

BlitZave is in alpha. Fixes land on `main`; please run the latest version.
