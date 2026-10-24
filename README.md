# BlitZave engine

The backend for BlitZave: an open-source, self-hosted personal finance app that
reflects your money *behavior* back to you, calmly. It's an ally, not a scorekeeper.

> **Status: pre-alpha.** Under construction; nothing to run yet.

## What it will do (MVP)

- Pull checking, savings, and credit card transactions from **your own** SimpleFIN
  Bridge account
- Store everything in one SQLite file on your machine (`~/BlitZave/blitzave.db`)
- Categorize transactions, and spot transfers so card payments aren't counted as
  spending
- Detect two behaviors — steady spending and paying yourself first — and serve them
  through a local API
- Take a daily snapshot and export everything as JSON whenever you want

There is no UI yet. A separate UI repo will come later; until then, the API's `/docs`
page is the interface.

## Privacy

- Runs on your laptop and listens on 127.0.0.1 only
- Bank access is read-only (SimpleFIN). It can never move money.
- Your SimpleFIN access credential stays in your OS keychain
- Nothing leaves your machine except the sync with SimpleFIN

## Requirements

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- A SimpleFIN Bridge account (a small yearly fee, paid to SimpleFIN)

## Run (once it exists)

```bash
uv sync
uv run blitzave    # → http://127.0.0.1:8742/docs
```

## Docs

The contributor and agent guides (`CLAUDE.md`, `AGENTS.md`) currently live in the
maintainer's workspace, one level up. They move into this repo before the first public
release.

## License

To be chosen before the first public release.
