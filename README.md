# Agent HQ

A local dashboard for watching AI coding agents across harnesses: Claude Code, Codex, opencode, Antigravity, Gemini CLI and Hermes (through the provider-usage tracker). It reads the logs those tools already write, stores them in one SQLite file, and shows sessions, KPIs, value and usage limits in a web UI on `127.0.0.1`.

Status:
- **Milestone 1:** backend. Done.
- **Milestone 2:** web UI with Dashboard, Agents, Limits and Settings. Done.
- **Milestone 3:** the isometric office view. Next.

Design: [spec](docs/superpowers/specs/2026-10-30-agent-hq-design.md), [backend plan](docs/superpowers/plans/2026-10-30-agent-hq-backend.md), [UI plan](docs/superpowers/plans/2026-10-31-agent-hq-ui.md), [approved mockup](docs/design/mockup-v2-office.html).

## Requirements

- Python 3.11 or newer. No third-party packages.
- Node 20 or newer, only to build the web UI once.
- Windows 11 first. Linux and macOS paths work too.

## Run

Build the UI once, and again after updating:

```bash
npm --prefix web ci
```

```bash
npm --prefix web run build
```

Then start it:

```bash
python -m hq
```

This does three things:
- Starts the collector, which reads new log lines every 5 s.
- Serves the UI and the API on `http://127.0.0.1:7420/`.
- Opens the browser with a **single-use launch code** in the URL fragment. The page trades it for the access token, so the token itself never appears in a URL or in browser history.

The code expires after 120 s. The token is kept in a file named `token` beside `hq.db` and reused on the next launch, so open pages keep working across restarts. `--rotate-token` replaces it.

If a page shows "Open Agent HQ from the link…", restart and use the new link, or add `#token=<contents of the token file>` to the URL.

## Web UI

- **Dashboard:**
  - Range: today, week or month. Group by harness, provider, model or account.
  - KPI tiles with change against the previous period.
  - Charts: tokens or value over time, tokens by model, tasks against errors.
  - Tables: top sessions, plan ROI, routing.
- **Agents:** every session with status filters. Clicking a row opens the inspector:
  - the current step;
  - project, duration, tokens and value;
  - the account's limit bars;
  - the newest 50 events;
  - subagents.
- **Limits:** one card per account with window bars, source badge (Measured, Blocked, Manual, Unknown), reset countdown, forecast and burn rate. Each card has a "Set manually" form.
- **Settings:** plans and prices, model API prices (unpriced models first), alert thresholds, default office skin, sources with reader health.
- **Banner and sidebar:** the most severe limit alert shows as a banner on every page, and the sidebar shows the four most constrained limits.

Everything refreshes live when new log lines arrive. Light and dark mode follow the system. The layout works from 1024 px wide.

For UI development, run `python -m hq --no-browser`, then `npm --prefix web run dev`. Open the dev URL with `#token=<token>`; the dev server forwards `/api` to port 7420.

| Flag | Meaning |
|---|---|
| `--port 7420` | API port |
| `--config DIR` | config folder (default `config/`) |
| `--db PATH` | database (default `%LOCALAPPDATA%\agent-hq\hq.db`, or `~/.local/share/agent-hq/hq.db`) |
| `--rotate-token` | replace the reusable token file with a fresh token |
| `--no-browser` | print the bootstrap code URL instead of opening a browser |
| `--once` | run one collection pass, print a health table, exit (exit 1 if any reader is `partial`) |

Run `--once` from PowerShell or cmd, not from a sandboxed shell, if you want the WSL Codex logs: `\\wsl.localhost\Debian\...` is only visible to normal Windows processes.

## What it reads

| Harness | Source | Gives |
|---|---|---|
| Claude Code | `~/.claude/projects/**/*.jsonl` (+ `subagents/`) | sessions, subagents, tool calls, tokens incl. cache |
| Codex | `~/.codex/sessions`, `\\wsl.localhost\Debian\root\.codex\sessions` | sessions, tool calls, tokens, **measured 5-hour and weekly limits** |
| opencode | `~/.local/share/opencode/opencode.db` (read-only; `session`, `message`, `part` only) | sessions, tool calls, tokens, provider/model |
| Antigravity | `~/.gemini/antigravity-cli/history.jsonl`, `conversations/*.db` | prompts and activity; tokens and model are **unknown** (binary step data) |
| Gemini CLI | `~/.gemini/tmp/*/chats` | chats; no token data in the logs |
| provider-usage | `~/.claude/provider-usage/usage.db` (read-only) | delegated calls, rate-limit blocks, Hermes runs |

## Config (`config/`)

- `sources.json`: which readers are on and their paths.
- `accounts.json`: your plans, monthly price, billing day, and rules mapping harness/provider/model to an account. Prices you have not filled in stay `null`, and ROI shows as unknown.
- `prices.json`: API price per model in USD per 1M tokens (`input`, `output`, `cache_read`, `cache_write`). Empty by default. Tokens of a model without a price are reported as **unpriced**, never guessed.

## API

`GET /api/overview`, `/api/sessions`, `/api/sessions/{id}`, `/api/kpis?range=today|week|month|custom&group_by=harness|provider|model|account`, `/api/limits`, `/api/recommend`, `/api/settings`, `/api/health`, `/api/stream` (Server-Sent Events). `POST /api/exchange` exchanges a valid bootstrap code for the token and requires a same-origin `Origin`. Other `/api/*` requests require the token in `X-HQ-Token`. `GET /api/stream` also accepts `?token=` for EventSource. POST and PUT require a same-origin `Origin`. `PUT /api/settings` validates settings and cannot change sources; edit `config/sources.json` and restart to change them.

## Privacy and safety

- Binds `127.0.0.1` only; requests with any other `Host` get 403 (blocks DNS rebinding).
- Read-only on every source: SQLite sources open with `mode=ro`; nothing is written to `usage.db` or any harness file.
- Source paths are checked at load and before access against `secrets`, `auth.json`, `oauth_creds.json`, `credentials`, `google_accounts.json`, `.env` and `.credentials.json`, including resolved paths and normalized segments. Directory walks skip forbidden directories.
- The browser stores the token in session storage. The launch URL contains a single-use code, not the token; a manually supplied `#token=` fragment is also supported.
- Static files contain no injected token or collected data. Recommendations use a temporary snapshot of the read-only source database.
- Previews are cut to 200 characters; full transcripts are not copied.
- Nothing leaves the machine.

## Tests

```bash
python -m unittest discover -s tests
```
