# Agent HQ

A local dashboard backend for watching AI coding agents across harnesses: Claude Code, Codex, opencode, Antigravity, Gemini CLI and Hermes (through the provider-usage tracker). It reads the logs those tools already write, stores them in one SQLite file, and serves sessions, KPIs, value and usage limits over a JSON API on `127.0.0.1`.

This is **Milestone 1 (backend)**. The web UI and the office view are later milestones. Design: [spec](docs/superpowers/specs/2026-10-30-agent-hq-design.md), [backend plan](docs/superpowers/plans/2026-10-30-agent-hq-backend.md).

## Requirements

- Python 3.11 or newer. No third-party packages.
- Windows 11 first. Linux and macOS paths work too.

## Run

```bash
python -m hq
```

This starts the collector (reads new log lines every 5 s) and the API on `http://127.0.0.1:7420/`, then opens the browser with a per-launch token in the URL fragment. The token is also written to a file named `token` beside `hq.db`.

| Flag | Meaning |
|---|---|
| `--port 7420` | API port |
| `--config DIR` | config folder (default `config/`) |
| `--db PATH` | database (default `%LOCALAPPDATA%\agent-hq\hq.db`, or `~/.local/share/agent-hq/hq.db`) |
| `--no-browser` | print the token-bearing URL instead of opening a browser |
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

`GET /api/overview`, `/api/sessions`, `/api/sessions/{id}`, `/api/kpis?range=today|week|month|custom&group_by=harness|provider|model|account`, `/api/limits`, `/api/recommend`, `/api/settings`, `/api/health`, `/api/stream` (Server-Sent Events). Every `/api/*` request requires the per-launch token in `X-HQ-Token`. `GET /api/stream` also accepts `?token=` for EventSource. POST and PUT require a same-origin `Origin`. `PUT /api/settings` validates settings and cannot change sources; edit `config/sources.json` and restart to change them.

## Privacy and safety

- Binds `127.0.0.1` only; requests with any other `Host` get 403 (blocks DNS rebinding).
- Read-only on every source: SQLite sources open with `mode=ro`; nothing is written to `usage.db` or any harness file.
- Source paths are checked at load and before access against `secrets`, `auth.json`, `oauth_creds.json`, `credentials`, `google_accounts.json`, `.env` and `.credentials.json`, including resolved paths and normalized segments. Directory walks skip forbidden directories.
- Static files contain no injected token or collected data. Recommendations use a temporary snapshot of the read-only source database.
- Previews are cut to 200 characters; full transcripts are not copied.
- Nothing leaves the machine.

## Tests

```bash
python -m unittest discover -s tests
```
