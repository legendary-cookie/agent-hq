# Agent HQ

[![CI](https://github.com/legendary-cookie/agent-hq/actions/workflows/ci.yml/badge.svg)](https://github.com/legendary-cookie/agent-hq/actions/workflows/ci.yml)

A local dashboard for the AI coding agents you already use: Claude Code, Codex, opencode, Antigravity, Gemini CLI, Kimi, DeepSeek Harness and more.

- **Watch:** it reads the logs those tools already write, on this PC and on your other machines over SSH, and stores them in one SQLite file. A web UI on `127.0.0.1` shows sessions, KPIs, value, usage limits, and an office where every agent is a small figure at a desk.
- **Run** (off until you turn it on): start agents in their own git worktrees, watch them live, talk to them, approve their edits and commands from an Inbox, and merge or discard what they did.
- **Orchestrate:** give a goal, review the plan a planner agent writes, and let a team of agents carry it out step by step on one branch.

It runs on your machine only, needs no account or cloud service, and uses no third-party Python packages. Windows 11 comes first; Linux and macOS work too.

Status:
- **Milestone 1:** backend. Done.
- **Milestone 2:** web UI with Dashboard, Agents, Limits and Settings. Done.
- **Milestone 3:** the office view, with isometric, top-down and tower skins. Done.
- **Milestone 4:** more sources (Kimi, DeepSeek Harness, Zylos, Antigravity tokens, a long-tail importer), DeepSeek balance and LiteLLM prices. Done.
- **Milestone 5a:** run mode, Dispatch. Start one-shot agent runs, watch them live, stop them, and review the diff, then merge or discard it. Done.
- **1.0:** Steer: follow-ups and an approvals inbox. Remote watching: agents on other machines, read over SSH. Done.
- **1.1:** Goals (Orchestrate), remote runs, office extras, and Ask me for opencode. Done.
- **Next:** see the [Roadmap](#roadmap).

Design: [spec](docs/superpowers/specs/2026-10-30-agent-hq-design.md), [Steer spec](docs/superpowers/specs/2026-11-03-agent-hq-steer-design.md), [remote watching spec](docs/superpowers/specs/2026-11-03-agent-hq-remote-watch-design.md), [run mode spec](docs/superpowers/specs/2026-11-03-agent-hq-run-mode-design.md), [1.1 spec](docs/superpowers/specs/2026-11-04-agent-hq-1.1-design.md), [backend plan](docs/superpowers/plans/2026-10-30-agent-hq-backend.md), [UI plan](docs/superpowers/plans/2026-10-31-agent-hq-ui.md), [approved mockup](docs/design/mockup-v2-office.html).

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

If a page shows the sign-in page on this computer, restart and use the new link, or add `#token=<contents of the token file>` to the URL. The browser stays signed in until you press **Sign out**.

### Sign in from another computer

Agent HQ still listens only on `127.0.0.1`. To reach it from elsewhere, put an HTTPS proxy such as Caddy on the same machine, then:

1. Set a password (12 characters or more): `python -m hq --set-password --db <path to hq.db>`. Only a salted scrypt hash is stored, in `login.json` beside `hq.db`.
2. Start with `--public-url https://your-address`. Only that address is accepted, and the proxy must send `X-Forwarded-Proto: https`.

Then open the address and sign in with the password.
- A sign-in lasts 30 days on that browser and survives restarts. Sessions are kept as token hashes in `sessions.json` beside `hq.db`.
- **Sign out** in the sidebar ends the session. Setting a new password signs everyone out.
- After 5 wrong passwords, sign-in is blocked for 30 s, then for twice as long after each further wrong one, up to 15 minutes.
- The start-up link and `#token=` still work on the machine itself, but not through the public address.
- A signed-in browser can use run mode, like the start-up link. Keep run mode off unless you trust everyone who can sign in.

Never expose Agent HQ through a plain HTTP proxy.

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
- With reduced motion on, figures move without walking, and emotes become still badges.
- **Thought bubbles:** a figure shows its current step, such as "Edit app.py", on hover, when selected and on new events. The bubble fades after 6 s.
- **Emotes:** ✓ when a turn ends, a red puff on an error, "!" on a new approval, and 💤 after 10 minutes idle.
- **Teams:** the agents of a goal share a team zone with a table.
- **Follow:** select a figure and press **Follow** to keep it centred, also through walks and floor changes. Esc stops.
- **Replay:** scrub through the last 24 hours at 1×, 10× or 60×, and press **Live** to come back. It opens at the first event in that window.
- **Edit layout:** drag zones and desks, and double-click a zone label to rename it. The layout is saved in your browser for each skin; **Reset layout** undoes it. Nothing is written on the server.

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
- **Banner:** the most severe limit alert shows as a banner on every page. The run pages (New run, Runs, Goals, Inbox) appear in the sidebar only when run mode is on.

Everything refreshes live when new log lines arrive. Light and dark mode follow the system, and the sidebar can switch them for this browser. The Geist font is bundled, so no font is fetched from the internet. The layout works from 1024 px wide.

For UI development, run `python -m hq --no-browser`, then `npm --prefix web run dev`. Open the dev URL with `#token=<token>`; the dev server forwards `/api` to port 7420. In the dev build only, `?synthetic=60` replaces the office's agents with 60 fake ones, for checking performance.

| Flag | Meaning |
|---|---|
| `--port 7420` | API port |
| `--config DIR` | config folder (default `config/`) |
| `--db PATH` | database (default `%LOCALAPPDATA%\agent-hq\hq.db`, or `~/.local/share/agent-hq/hq.db`) |
| `--rotate-token` | replace the reusable token file with a fresh token |
| `--no-browser` | print the bootstrap code URL instead of opening a browser |
| `--once` | run one collection pass, print a health table, exit (exit 1 if any reader is `partial`) |
| `--set-password` | set the sign-in password from a prompt, then exit |
| `--public-url https://host` | also accept requests for this address, from an HTTPS proxy |

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

### Steer: follow-ups and approvals

- **Follow-ups.** When a run is idle or finished, write a follow-up in the run's conversation view and press **Send**.
  - **Claude Code** takes it live, over the open connection.
  - **Codex, opencode and Antigravity** resume the same session.

  A follow-up keeps the run's level, worktree and account, and starts a new turn. It also works after Agent HQ restarts, and after a run timed out or failed.
- **Ask me.** At this level the agent works in its own worktree, and every edit or command waits for you in the **Inbox**: a sidebar counter, with a "Needs you" figure in the office.
  - **Allow once.**
  - **Allow for this run:** for file edits, the same folder inside the run's worktree. For commands, only read-only ones (`ls`, `cat`, `grep`, `rg`, `head`, `tail`, `wc`, `diff`, PowerShell `Get-Content`, `Get-ChildItem` and the like); everything else, including `npm`, `git` and `make`, asks every time.
  - **Deny:** with an optional note, which goes to the agent.

  Unanswered requests are denied after 15 minutes. You can change that with `approval_timeout_min` in `run.json`.
- **Always asks**, even with an allow-for-this-run rule: `rm`, `del`, `rmdir`, `git push`, `git reset`, `curl`, `wget`, `ssh`, `scp`, `nc`, `powershell`, `pwsh`, `sudo`, and any command containing `|`, `;`, `&`, `>`, `<`, `` ` ``, `$(` or a newline.
- **Where Ask me works:** Claude Code and opencode.
  - opencode runs over its ACP protocol (`opencode acp`). Each edit or command it asks about becomes an Inbox item. An edit that touches several files lists them all and always asks.
  - At Ask me, opencode asks for everything except reading, searching and listing files in its worktree. Reading `.env` files still asks.
  - Codex headless can't pause, and Antigravity headless turns permission requests down by itself, so neither offers Ask me.
- **Live follow-ups:** Claude Code, opencode and Antigravity keep the run's process open between turns. Codex resumes its session.
- **opencode at Edit** can run commands in its worktree without asking, and without a sandbox. Codex's Edit level uses its own sandbox, and Claude Code's Edit level can't run commands. Pick Ask me if you want to see opencode's commands first.
- **Time limit:** it counts each turn's working time, and pauses while a run is idle. An idle live run closes after 30 minutes; a follow-up resumes it.

### Goals: a team of agents on one branch

Open **Goals** and press **New goal**. Choose:
- the goal text and a project;
- the **highest level** steps may use: Read-only, Ask me or Edit (default Edit). Full auto is never used for planned steps;
- a step limit (default 6, at most 10), a total time limit (default 90 min, at most 4 h) and how many steps may run at once;
- the planner tool (default Claude Code).

What happens:
1. **Planning:** a read-only planner run reads the project and answers with a plan, a JSON list of steps. Each step has a title, a prompt, a kind (`code`, `review` or `research`), its dependencies and, if it wants, a tool.
2. **Review:** Agent HQ treats the plan as untrusted data. It checks ids, dependencies, cycles, sizes and tools, and sets each step's level itself: `code` steps get the goal's highest level, `review` and `research` steps are read-only. You can edit or delete any step. Nothing runs until you press **Start plan**.
3. **Steps:** a step starts when its dependencies are done, up to the parallel limit. Each step is a normal run in its own worktree, started from the goal branch `hq/goal-<id>`. When it finishes, its commit is merged into the goal branch, in Agent HQ's own worktree and never in your checkout.
4. **Finish:** the goal page shows the whole diff. **Merge into project** merges exactly the commit you saw; **Discard goal** removes the branch.

If a step fails or its merge conflicts, the goal waits for you: **retry**, **skip**, or **Stop goal**. The time limit stops everything. Goals that were planning or running when Agent HQ quit show as stopped, "interrupted by restart". Goals waiting for review survive a restart.

Checked on this PC, 2026-11-05: Claude Code planned a three-step goal on a small Python project. Codex added `mul()`, then wrote `test_calc.py`, then reviewed both at read-only. The goal diff showed exactly those two changes. After **Merge into project**, the project's checkout was clean and `python test_calc.py` passed.

**Tools on this PC**, checked with real runs on 2026-11-03:

| Tool | Status |
|---|---|
| Claude Code, Codex (WSL Debian), opencode, Antigravity | Tested: each run, its session link and its token totals were checked |
| Kimi | Hidden until checked: not logged in on this PC |
| DeepSeek Harness | Hidden until checked: no API key for its default route |
| Gemini CLI | Hidden until checked: its free tier no longer accepts this client |
| Hermes | Disabled: its shell commands run in its configured folder even with `--in` |

To see untested tools anyway, set `"show_untested": true` in `run.json`.

## Remote hosts

Agent HQ can show agents running on other machines: Linux servers and VMs, Windows PCs, Macs, and Docker containers on them. It reads them over your own SSH.

1. Make sure `ssh <name>` works without a password: a key or ssh-agent, plus an entry in `~/.ssh/config`. The host key must already be in `known_hosts`. Machines on Tailscale or another VPN work the same way; only the host name differs.
2. List the machine in `config/hosts.json` (copy `config/hosts.json.example`):
   ```json
   {"hosts": [{"name": "homeserver", "ssh": "homeserver", "os": "linux", "sources": ["claude_code", "codex"]}]}
   ```
   Fields:
   - `os` is `linux`, `macos` or `windows`.
   - Add `"container": "<name>"` to read a Docker container on that machine.
   - `interval_s` (30–3600, default 60) sets how often it's read.
3. Restart Agent HQ.

How it works:
- Every interval, Agent HQ runs one `ssh` command. It sends a small read-only scanner to `python3 -` on the machine, so the machine needs Python 3.
- The scanner returns only new bytes of the agent log files. Agent HQ checks every path and size, writes the bytes to a local mirror under `<data folder>/remote/<name>/`, and runs the same readers it uses locally.
- Sessions show up with a host badge, a host filter on Agents and Dashboard, and their own office zone or floor. Settings shows each host's health.

Safety:
- Agent HQ never reads, copies or passes keys or passwords. It runs your `ssh` with `BatchMode=yes`, `StrictHostKeyChecking=yes`, and no agent or X11 forwarding.
- Everything a host sends back is treated as untrusted:
  - paths must stay inside that host's mirror folder;
  - each pass is capped at 20 MB of logs and one database of at most 40 MB;
  - forbidden files (`auth.json`, `.credentials.json`, `.env`, `secrets` and so on) are skipped on both sides.
- An unreachable or misbehaving host only affects its own health row. It backs off up to 10 minutes and retries.

### Zylos message metrics (no message text)

To count messages on a remote Zylos agent, add the `zylos_metrics` source:

```json
{"hosts": [{"name": "agent-1", "ssh": "zylos@agent-1", "os": "linux", "interval_s": 300, "sources": ["zylos_metrics"]}]}
```

- The scanner opens `~/zylos/comm-bridge/c4.db` read-only and sends only the id, direction, channel, failed flag and timestamp of each message. It never selects the message text, so the text can't leave the machine, and the "Show Zylos message previews" setting has no effect on this source.
- No file is copied, so the database size limit above doesn't apply. A large backlog arrives 20,000 messages per pass, and the host shows as catching up.
- You get message counts and activity, not tokens or costs. Those come from the `claude_code` source, which copies full transcripts into the mirror.
- The messages are stored as one ended session per host, `<host>/zylos:comm`. The Agents page leaves it out, as it is a log and not a session to open.
- If the database is replaced by one with a lower highest id, Agent HQ reads the new one from the start and keeps the old counts. A replacement that already has a higher id than the last one read isn't detected, so its earlier messages are skipped.

Checked on this PC, 2026-11-03: WSL Debian, reached over SSH as a stand-in server.
- 90 Codex sessions, with 68,712,888 tokens.
- They match exactly what the local reader reports for the same files.
- The mirror is byte-identical to the remote files (SHA-256).

### Remote runs

You can also start runs on a Linux or macOS host:
1. In `hosts.json`, give the host `"run": true` and a list of projects it may use: `"projects": [{"name": "myapp", "path": "/srv/myapp"}]`.
2. In **Run**, pick the host, then one of its projects.

How it works:
- Agent HQ builds the launch locally, with the same launchers as local runs, and sends one fixed helper script over `ssh` (`python3 -`).
- On the host, the helper checks the project against that host's list, creates a worktree with the same git rules, starts the tool without a shell, and streams its output back.
- Stop, diff, merge and discard work as for local runs. The worktrees live under `~/.agent-hq/worktrees` on the host. If the connection drops, the run is marked interrupted and its processes on the host are ended.

Limits:
- **Tools:** Codex and Claude Code, and only those tested on this PC.
- **Levels:** no Full auto, and Ask me always asks: there are no allow-for-this-run rules, because remote paths can't be checked from here.
- **Accounts:** a local account profile is not sent to the host. The tool uses the host's own login.
- **Hosts:** Windows hosts and containers can be watched, but not used for runs.

Checked on this PC, 2026-11-04: a Codex edit run on WSL Debian over SSH. Its session linked through remote watching, its diff was right, and the merge landed in the host's checkout, which stayed clean.

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
- `accounts.json`: your plans, monthly price, billing day, and rules mapping harness/provider/model to an account. On the first start it is copied from `accounts.example.json`, and it is not tracked by git, so your plans stay private. Prices you have not filled in stay `null`, and ROI shows as unknown.
- `prices.json`: API price per model in USD per 1M tokens (`input`, `output`, `cache_read`, `cache_write`). Empty by default. Tokens of a model without a price are reported as **unpriced**, never guessed.

## API

Steer routes:
- `GET /api/approvals?state=waiting` and `POST /api/approvals/{id}`
- `POST /api/runs/{id}/message/prepare` and `POST /api/runs/{id}/message`

Goal routes:
- `GET /api/goals` and `GET /api/goals/{id}`, plus `/diff`
- `POST /api/goals/prepare` and `POST /api/goals`
- `PUT /api/goals/{id}/plan`, then `POST /api/goals/{id}/start/prepare` and `POST /api/goals/{id}/start`
- `POST /api/goals/{id}/steps/{step}/retry` and `/skip`, and `POST /api/goals/{id}/stop`, `/merge` and `/discard`

Office replay: `GET /api/timeline?from=&to=` returns up to 20,000 events from a window of at most 7 days, with only the columns the office needs.

Run mode routes:
- `GET /api/runs/options`
- `POST /api/runs/prepare` and `POST /api/runs`
- `GET /api/runs` and `GET /api/runs/{id}`, plus `/output` and `/diff`
- `POST /api/runs/{id}/stop`, `/merge` and `/discard`

They need the token, a same-origin `Origin`, and the in-memory `X-HQ-Run-Key` header from `/api/exchange`. Starting a run also needs a single-use confirm code from `prepare`, bound to that exact request.

`GET /api/overview`, `/api/sessions`, `/api/sessions/{id}`, `/api/kpis?range=today|week|month|custom&group_by=harness|provider|model|account`, `/api/limits`, `/api/recommend`, `/api/settings`, `/api/health`, `/api/stream` (Server-Sent Events). `POST /api/exchange` exchanges a valid bootstrap code for the token and requires a same-origin `Origin`. Other `/api/*` requests require the token in `X-HQ-Token`. `GET /api/stream` also accepts `?token=` for EventSource. POST and PUT require a same-origin `Origin`. `PUT /api/settings` validates settings and cannot change sources; edit `config/sources.json` and restart to change them.

## Privacy and safety

- Binds `127.0.0.1` only; requests with any other `Host` get 403 (blocks DNS rebinding). With `--public-url`, that one address is also accepted, with its own `Origin`.
- Read-only on every source: SQLite sources open with `mode=ro`; nothing is written to `usage.db` or any harness file.
- Source paths are checked at load and before access against `secrets`, `auth.json`, `oauth_creds.json`, `credentials`, `google_accounts.json`, `.env` and `.credentials.json`, including resolved paths and normalized segments. Directory walks skip forbidden directories.
- The browser stores the token in local storage, so it stays signed in until **Sign out**. The launch URL contains a single-use code, not the token; a manually supplied `#token=` fragment is also supported.
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
  - **Goals:** the planner's output is untrusted. Levels are capped and tools come from an allowlist. Only the agent's own top-level words count, never tool output, hooks or subagents. Start plan takes a confirm code bound to the exact plan.
  - **Remote runs:** every value sent is checked here and again on the host. Output, paths, diffs and session ids from the host are treated as untrusted.
- Otherwise nothing leaves the machine.

## Roadmap

Planned, roughly in this order. Nothing here is built yet.

**Remote machines**
- **Tailscale and other VPNs as a first-class connection.** Today a machine on a VPN already works if `ssh <name>` works; this adds setup help and checks for it.
- **An optional relay** for busy servers: a small Agent HQ helper on the server, streaming over an SSH tunnel, instead of one `ssh` call per pass.
- **More tools for remote runs:** opencode and Antigravity, besides Codex and Claude Code.
- **Remote runs on Windows hosts and in containers.** Today these can only be watched.
- **Remote accounts in the pool:** a host's own logins and limits, used by **Pool: best** and the guard.

**Running agents**
- **More tools in run mode:** Kimi, DeepSeek Harness and Gemini CLI, each once it has been checked with real runs. Hermes, once its shell commands stay in the project folder.
- **Ask me for Codex and Antigravity,** if their headless modes gain a way to pause for a permission.
- **A sandbox for opencode at Edit,** so its commands are contained the way Codex's are.
- **Scheduled runs and goals,** for example a nightly review.
- **Approval rules you can save across runs,** opt-in and per project.

**Polish and known issues**
- A follow-up refused by the guard has to be confirmed again; it should keep your answer.
- A process that a run leaves behind and that holds stderr open keeps one reader thread alive after the run (no hang).
- Layouts below 1024 px wide: the Goals and Runs tables get cramped next to the detail panel.
- A smaller web bundle, by loading the office skins on demand.

**Project**
- A contributing guide.

**Not planned:** a hosted or multi-user version, reading limits by scraping provider web dashboards, and managing SSH keys or passwords. Agent HQ stays on `127.0.0.1` and uses your own logins.

## Tests

```bash
python -m unittest discover -s tests -t .
```

```bash
npm --prefix web test
```

The remote-run helper tests need a POSIX system, so they skip on Windows; run them on Linux or in WSL. CI runs everything on every push and pull request: Python 3.11 and 3.14 on Linux, Python 3.14 on Windows, and the web tests and build. The tests never start a real agent, never use `ssh`, and never call a network API.

## License

MIT, see [LICENSE](LICENSE). Bundled third-party data is listed in [NOTICE](NOTICE).
