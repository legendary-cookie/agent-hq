# Agent HQ

A local dashboard for watching AI coding agents across harnesses: Claude Code, Codex, opencode, Antigravity, Gemini CLI and Hermes (through the provider-usage tracker). It reads the logs those tools already write, stores them in one SQLite file, and shows sessions, KPIs, value and usage limits in a web UI on `127.0.0.1`. With run mode turned on, it can also start, watch and stop agents itself.

Status:
- **Milestone 1:** backend. Done.
- **Milestone 2:** web UI with Dashboard, Agents, Limits and Settings. Done.
- **Milestone 3:** the office view, with isometric, top-down and tower skins. Done.
- **Milestone 4:** more sources (Kimi, DeepSeek Harness, Zylos, Antigravity tokens, a long-tail importer), DeepSeek balance and LiteLLM prices. Done.
- **Milestone 5a:** run mode, Dispatch. Start one-shot agent runs, watch them live, stop them, and review the diff, then merge or discard it. Done.
- **Next:** Steer (M5b), Orchestrate (M5c), and remote hosts.

Design: [spec](docs/superpowers/specs/2026-10-30-agent-hq-design.md), [run mode spec](docs/superpowers/specs/2026-11-03-agent-hq-run-mode-design.md), [backend plan](docs/superpowers/plans/2026-10-30-agent-hq-backend.md), [UI plan](docs/superpowers/plans/2026-10-31-agent-hq-ui.md), [approved mockup](docs/design/mockup-v2-office.html).

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

**Office** (the start page) shows every agent from the last 24 hours as a small figure in an office:
- One zone per harness, tinted in that harness's colour and labelled with its agent count, plus a break room.
- Figures are coloured by harness. Their state shows as a ring and marker:
  - **Working:** green ring and a gentle typing bob.
  - **Needs you:** orange ring and a "!" bubble.
  - **Error:** red ring and a red puff.
  - **Idle:** grey ring. The figure waits in the break room and keeps its desk reserved, shown dimmed.
- Agents keep their desk as others come and go.
- A new subagent, or a Claude → Codex/Antigravity/Hermes/opencode delegation, makes a figure walk between zones. A small ticker shows the latest delegation.
- Hover shows the agent's title. Click opens the same inspector as the Agents page.
- Skins: **Isometric** (default), **Top-down** and **Tower**, one floor per harness with a lift. Your choice is remembered; the default is set in Settings.
- Harness and account filters. A hidden list of agents is available to screen readers.
- With reduced motion on, figures move without walking.

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

For UI development, run `python -m hq --no-browser`, then `npm --prefix web run dev`. Open the dev URL with `#token=<token>`; the dev server forwards `/api` to port 7420. In the dev build only, `?synthetic=60` replaces the office's agents with 60 fake ones, for checking performance.

| Flag | Meaning |
|---|---|
| `--port 7420` | API port |
| `--config DIR` | config folder (default `config/`) |
| `--db PATH` | database (default `%LOCALAPPDATA%\agent-hq\hq.db`, or `~/.local/share/agent-hq/hq.db`) |
| `--rotate-token` | replace the reusable token file with a fresh token |
| `--no-browser` | print the bootstrap code URL instead of opening a browser |
| `--once` | run one collection pass, print a health table, exit (exit 1 if any reader is `partial`) |

Run `--once` from PowerShell or cmd, not from a sandboxed shell, if you want the WSL Codex logs: `\\wsl.localhost\Debian\...` is only visible to normal Windows processes.

## Run mode

Run mode is **off** until you turn it on. Agent HQ never writes these files; edit them and restart.

1. `config/run.json` (copy `config/run.json.example`):
   ```json
   {"run_mode": true, "max_concurrent": 3, "default_time_limit_min": 30, "max_time_limit_min": 240, "pass_env": {}}
   ```
   `pass_env` lets one tool receive an API key, named by its variable name only, for example `{"dsh": ["DEEPSEEK_API_KEY"]}`. No other variable that looks like a key, token, secret or password reaches a launched agent.
2. `config/projects.json` (copy `config/projects.json.example`) lists the folders runs may work in. No other folder can be used.
3. **Account pool (optional):**
   - In `config/accounts.json`, an account can list `profiles`, each with its own config folder for that tool. Example for three ChatGPT logins: `{"id": "chatgpt-2", "tool": "codex", "config_dir": "/root/.codex-chatgpt-2"}`.
   - Log in once per profile in a terminal. Agent HQ only points the tool at the folder (`CODEX_HOME`, `CLAUDE_CONFIG_DIR`) and never reads login files.
   - Check that each provider's terms allow several accounts.

Then open Agent HQ **from its launch link** and press **Run**. Run mode needs a key the page receives only at launch.

| Field | Choices |
|---|---|
| Level | **Read-only**; **Edit project folder** (default); **Full auto**, which asks twice and is offered only where the tool can be contained |
| Guard | **Warn**; **Block and suggest** (default); **Auto-route** |
| Account | **Pool: best** (the profile with the most measured room left) or a specific profile |
| Time limit | 1 to 240 minutes (default 30) |

The guard modes:
- **Warn:** shows warnings but never stops a run.
- **Block and suggest:** a blocked model can't start, a window at 80% or more shows a warning, and provider-usage's suggestion is shown.
- **Auto-route:** provider-usage's suggestion picks the tool, model and account.

How a run works:
- **Its own branch:** an editing run works in its own git worktree and branch (`hq/run-<id>`) under the data folder. Your checkout is never touched.
- **The Runs page shows:**
  - live output;
  - the linked session, whose tokens and value come from the normal readers, so nothing is counted twice;
  - the run's **diff**, once it ends.
- **Merge or discard:** **Merge into project** merges exactly the commit you saw. **Discard branch** removes the worktree. Nothing is pushed.
- **Stopping:** Stop, the time limit and quitting Agent HQ all end the agent and everything it started.
- **Logging:** every run is logged to provider-usage. A run that hits a real rate limit marks its account blocked for an hour.

**Tools on this PC**, checked with real runs on 2026-11-03:

| Tool | Status |
|---|---|
| Claude Code, Codex (WSL Debian), opencode, Antigravity | Tested: each run, its session link and its token totals were checked |
| Kimi | Hidden until checked: not logged in on this PC |
| DeepSeek Harness | Hidden until checked: no API key for its default route |
| Gemini CLI | Hidden until checked: its free tier no longer accepts this client |
| Hermes | Disabled: its shell commands run in its configured folder even with `--in` |

To see untested tools anyway, set `"show_untested": true` in `run.json`.

## What it reads

| Harness | Source | Gives |
|---|---|---|
| Claude Code | `~/.claude/projects/**/*.jsonl` (+ `subagents/`) | sessions, subagents, tool calls, tokens incl. cache |
| Codex | `~/.codex/sessions` (including compressed `.jsonl.zst`), `\\wsl.localhost\Debian\root\.codex\sessions` | sessions, subagents, tool calls, tokens, **measured 5-hour and weekly limits** |
| opencode | `~/.local/share/opencode/opencode.db` (read-only; `session`, `message`, `part` only) | sessions, tool calls, tokens, provider/model |
| Antigravity | `~/.gemini/antigravity-cli/history.jsonl`, `conversations/*.db` | prompts, activity, **tokens and model** (decoded from `gen_metadata`) |
| Gemini CLI | `~/.gemini/tmp/*/chats` | chats; no token data in the logs |
| provider-usage | `~/.claude/provider-usage/usage.db` (read-only) | delegated calls, rate-limit blocks, Hermes runs |
| Kimi Code / Kimi CLI | `~/.kimi-code/sessions`, `~/.kimi/sessions` | sessions, subagents, tokens (provider `moonshot`) |
| DeepSeek Harness | `$DSH_HOME/sessions` or `~/.dsh/sessions` (`session.v4.jsonl.zstd`, v3 too) | sessions, subagents, tool calls, prompts, turns, tokens, provider/model |
| Zylos | `~/zylos/comm-bridge/c4.db`; Claude/Codex sessions under `~/zylos` | Telegram, Lark and Discord messages as events; message text only if you turn previews on |
| Long tail (optional) | llm-usage-metrics 0.12.1, installed from Settings | Copilot CLI, Goose, Amp, Qwen, Cline, Roo, Kilo, Droid, pi, OpenClaw: sessions and tokens only (status `active` from recency) |

Readers that have been checked against real logs on this PC show plain health. The others say "untested on this PC". Each reader turns on by itself when its folder exists. The importer turns on when it's installed in your data folder.

**DeepSeek API balance (optional):**
- Set `DEEPSEEK_API_KEY`, or put the key in `<data folder>/secrets/deepseek_api_key`.
- A "DeepSeek API" card then appears on Limits, refreshed every 15 min, with an alert below the threshold you set in Settings.

**Prices:**
- `config/prices.litellm.json` is a bundled LiteLLM price snapshot (MIT; see `NOTICE`), refreshed only by running `python tools/update_prices.py` and committing the result.
- Your `config/prices.json` always wins.
- Settings shows where each model's price comes from.

## Config (`config/`)

- `sources.json`: which readers are on and their paths.
- `accounts.json`: your plans, monthly price, billing day, and rules mapping harness/provider/model to an account. Prices you have not filled in stay `null`, and ROI shows as unknown.
- `prices.json`: API price per model in USD per 1M tokens (`input`, `output`, `cache_read`, `cache_write`). Empty by default. Tokens of a model without a price are reported as **unpriced**, never guessed.

## API

Run mode routes:
- `GET /api/runs/options`
- `POST /api/runs/prepare` and `POST /api/runs`
- `GET /api/runs` and `GET /api/runs/{id}`, plus `/output` and `/diff`
- `POST /api/runs/{id}/stop`, `/merge` and `/discard`

They need the token, a same-origin `Origin`, and the in-memory `X-HQ-Run-Key` header from `/api/exchange`. Starting a run also needs a single-use confirm code from `prepare`, bound to that exact request.

`GET /api/overview`, `/api/sessions`, `/api/sessions/{id}`, `/api/kpis?range=today|week|month|custom&group_by=harness|provider|model|account`, `/api/limits`, `/api/recommend`, `/api/settings`, `/api/health`, `/api/stream` (Server-Sent Events). `POST /api/exchange` exchanges a valid bootstrap code for the token and requires a same-origin `Origin`. Other `/api/*` requests require the token in `X-HQ-Token`. `GET /api/stream` also accepts `?token=` for EventSource. POST and PUT require a same-origin `Origin`. `PUT /api/settings` validates settings and cannot change sources; edit `config/sources.json` and restart to change them.

## Privacy and safety

- Binds `127.0.0.1` only; requests with any other `Host` get 403 (blocks DNS rebinding).
- Read-only on every source: SQLite sources open with `mode=ro`; nothing is written to `usage.db` or any harness file.
- Source paths are checked at load and before access against `secrets`, `auth.json`, `oauth_creds.json`, `credentials`, `google_accounts.json`, `.env` and `.credentials.json`, including resolved paths and normalized segments. Directory walks skip forbidden directories.
- The browser stores the token in session storage. The launch URL contains a single-use code, not the token; a manually supplied `#token=` fragment is also supported.
- Static files contain no injected token or collected data. Recommendations use a temporary snapshot of the read-only source database.
- Previews are cut to 200 characters; full transcripts are not copied. Zylos chat text is not stored unless you turn on "Show Zylos message previews".
- **Network:** the only outbound request is the optional DeepSeek balance check.
  - It goes to `https://api.deepseek.com/user/balance`, with a 10 s timeout, and only when a key exists.
  - Redirects are refused, so the key can't be forwarded elsewhere.
  - The key is never logged, shown, or returned by the API.
- **Importer:** installing it runs `npm install --ignore-scripts llm-usage-metrics@0.12.1` into your data folder (only when you click the button).
  - Runs use a fixed argument list with no shell and a timeout.
  - Their environment has variables matching KEY, TOKEN, SECRET or PASSWORD removed.
  - npm must come from your PATH, not the current folder.
- **Run mode** (off by default) launches programs, so it has its own rules:
  - Tools are resolved once from PATH, never from the current folder, a project or a worktree.
  - Argument lists are fixed and no shell is used. npm `.cmd` shims are resolved to the real program, so no prompt passes through `cmd.exe`.
  - Only allowlisted projects can be used, checked by real path (symlinks, junctions, short names).
  - A launched agent gets a minimal environment: only the variables tools need to start, plus `pass_env` names.
  - Starting a run needs the in-memory run key and a single-use confirm code. The run key is never written to disk, so an agent that reads Agent HQ's token file still can't start runs.
  - Git never finds a repository from inside a worktree. Agent HQ passes the repository explicitly, turns off hooks and fsmonitor, and refuses to continue if a run changed the worktree's `.git`.
  - Output is kept in memory only (last 500 lines, 64 KB per line). The database keeps the status and a 200-character prompt preview.
- Otherwise nothing leaves the machine.

## Tests

```bash
python -m unittest discover -s tests
```
