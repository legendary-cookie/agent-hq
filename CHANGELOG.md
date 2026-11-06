# Changelog

## Unreleased

Added
- **Fleet pages: Overview, Agents, Usage and Insights.** Overview opens with a one-sentence status and a grid of every remote agent's activity over 24 hours, 7 days or 30 days. Agents is a filterable table with details per agent. Usage ranks agents and models, with projected 30 days, spending with no messages, top-tier share, cost per task, cache share and error rate. Insights shows reply times, problems, task times, top tools and busy hours. New routes: `GET /api/fleet`, `/api/insights`, `/api/alerts` and `/api/brand`.
- **Graphs** on Overview, Usage and Insights, each with a "Graphs on/off" switch that this browser remembers per page.
- **Optional `config/fleet.json`** sets the app name and each agent's display name, owner and group. The Office uses those names too.
- **Search anywhere with Ctrl K.** The old Agents page is now Sessions, and Overview is the start page.
- **New visual design.** Minimal and monochrome, with colour reserved for status, in light and dark, with a switch in the sidebar. Geist is bundled, so no font is fetched from the internet. The inspector opens as a sheet over the page.
- **Zylos message metrics for remote hosts.** A new `zylos_metrics` source in `hosts.json` counts messages on remote Zylos agents. The scanner sends only message metadata (id, direction, channel, failed, time), never the text, and no file is mirrored.
- **Password sign-in for use behind an HTTPS proxy.** `--public-url https://host` accepts that one address, and `--set-password` sets the password from a terminal. A salted scrypt hash is stored, never the password. Sessions last 30 days and survive restarts, sign-out ends one, and wrong passwords lock sign-in for a growing time. The start-up link still works on the machine itself, but not through the public address.
- A new sign-in page replaces "Open Agent HQ from the link…", and the sidebar has **Sign out**.

Changed
- The browser keeps its token in `localStorage` instead of `sessionStorage`, so closing the tab no longer signs you out. A token left in tab storage is moved across once.

Fixed
- Much faster on large databases. Status lookups skip sessions older than a day, fleet answers are cached, and every page loads `/api/alerts` instead of the full overview.
- The Dashboard no longer switches from dollars to tokens because a row with no tokens has no price.
- The Run button in the inspector shows only when run mode is on.
- The server reads a request's body before refusing it, so clients get the error rather than a reset connection.

## 1.1.0 (2026-11-05): Goals, remote runs, office extras

Added
- **Goals (Orchestrate).** Give a goal. A read-only planner run writes a plan; you review and edit it, then start it.
  - Steps run as normal runs in order of their dependencies, each in its own worktree from the goal branch `hq/goal-<id>`. Each finished step is merged into the goal branch inside Agent HQ's own worktree.
  - You merge the whole goal once, at the end, after seeing its diff.
  - The plan is untrusted. Levels are capped by kind and by the goal (never Full auto), tools come from an allowlist, and Start plan takes a confirm code bound to the exact plan.
  - A failed step or a merge conflict waits for you, with retry, skip and stop.
- **Remote runs** on Linux and macOS hosts: Codex and Claude Code, over the same SSH as remote watching, with a fixed helper script, a per-host project list, stop, diff, merge and discard.
- **Office extras:** thought bubbles, emotes, team zones for goals, follow camera, a 24-hour replay (`GET /api/timeline`), and a layout editor saved in your browser.
- **opencode Ask me and live follow-ups** over ACP (`opencode acp`), and **Antigravity live follow-ups**.
- A first start creates `config/accounts.json` from `accounts.example.json`. Your own `accounts.json` is no longer tracked by git.
- MIT `LICENSE`.

Verified on this PC (2026-11-04/05)
- **A real goal:** Claude Code planned three steps for a small Python project, and Codex carried them out: add `mul()`, write `test_calc.py`, review read-only. The goal diff held exactly those changes. After the merge the checkout was clean and `python test_calc.py` passed.
- **A goal across a restart:** the goal showed as stopped, "interrupted by restart".
- **opencode Ask me:** an allowed write created `oc.txt`, a denied `rm calc.py` did not run, and the checkout stayed clean.
- **Antigravity live follow-up:** one process answered both turns, in order.
- **Remote Codex edit run** on WSL Debian over SSH: the session id was prefixed with the host, the diff was right, and the merge landed in the host's checkout, which stayed clean.
- **Tests:** 582 Python tests pass on Windows (19 skipped: POSIX-only remote tests, which pass in WSL) and 158 web tests pass.
- **Independent security review** (Claude Opus, read-only; Codex wrote most of 1.1): no high-severity findings; two medium and four low, all fixed and re-checked.

Fixed during the real checks
- **Planner stuck:** a live Claude planner went idle and kept the goal in planning. Goal runs now end after their turn.
- **Plan not found:** the planner's plan sat inside stream JSON. Plans are now read only from the agent's own words.
- **opencode `"model": null`:** opencode refused to start without a chosen model. The model is now left out unless you pick one.
- **opencode error replies:** an API error left the run running until its time limit. The run now fails, and the agent is ended if it ignores that.
- **opencode Inbox:** edit requests showed no file, and commands skipped the command rules. Edits now show their path, and commands go through the same rules as Claude Code's.
- **Replay:** it opened on an empty moment, before the first event.
- **Goals page:** now styled like Runs. Primary buttons are readable in dark mode.

Security fixes from the review
- An opencode edit touching several files was checked by its first path only. Every file now shows, and such edits always ask.
- opencode's default permission is allow-all, so Ask me now asks for everything except local reads (`.env` reads still ask).
- Remote runs get no allow-for-this-run rules, because remote paths can't be checked locally.
- Local profile folders are no longer sent to remote hosts.
- Claude subagent messages can no longer supply a goal plan.
- A code fence inside a step prompt no longer breaks a goal at start.

## 1.0.0 (2026-11-04): Steer and remote watching

Added
- **Steer.** Talk to the agents Agent HQ starts.
  - **Follow-ups:**
    - Claude Code takes them live, over the open connection.
    - Codex, opencode and Antigravity resume the same session.
    - A follow-up keeps the run's level, worktree and account, also after a restart, a time-out or a failed turn.
  - **Ask me level and Inbox:**
    - The agent's edits and commands wait in the Inbox for **Allow once**, **Allow for this run** or **Deny** (with an optional note to the agent).
    - Unanswered requests are denied after 15 minutes (`approval_timeout_min`).
    - A list of commands always asks, even with a rule.
    - Only Claude Code supports Ask me so far.
  - **Conversation view:** prompts, agent replies, tool calls and approvals, with a raw-output toggle.
  - **Time limits:** they count each turn's working time and pause while a run is idle; idle live runs close after 30 minutes.
- **Remote watching.** Agents on other machines (Linux, Windows, macOS, Docker), read over your own SSH (`config/hosts.json`).
  - A fixed read-only scanner sends new log bytes. Agent HQ validates them into a local mirror and runs the same readers on it.
  - Sessions are namespaced `<host>/<id>`, with a host filter, host badge, office zone per host, and host health in Settings.

Verified on this PC (2026-11-03/04)
- **Remote watching**, with WSL Debian reached over real SSH as a stand-in server:
  - 90 Codex sessions and 68,712,888 tokens, identical to the local reader for the same files, with 0 mismatched sessions;
  - the mirror is byte-identical to the remote files (SHA-256, 90 of 90).
- **Ask me** in the real UI with Claude Code:
  - the inbox showed `Write hello.txt` (allowed) and `rm calc.py` (denied with a note);
  - on disk, hello.txt was created, calc.py was untouched, and the project checkout stayed clean;
  - the agent received the note word for word.
- **Follow-ups:**
  - real follow-ups on Claude Code, Codex, opencode and Antigravity, each answered in its original session;
  - repeated after a server restart, and with the session ids passed fused to their flags.
- **Tests:** 500 Python and 144 web tests pass on Windows.

Fixed during the real checks
- **Inbox 403:** `GET /api/approvals` required an `Origin` header that browsers don't send on same-origin GETs, so the Inbox always got 403.
- **Codex follow-ups:** `codex exec resume` has no `-s`; the sandbox is now set with `-c sandbox_mode`, so a resume never falls back to a default.
- **Resuming:**
  - Codex, opencode and Antigravity runs couldn't be continued after a restart;
  - timed-out and failed runs couldn't be continued;
  - the time limit counted idle time.
- **Restart status:** idle and waiting runs kept their status after a restart.
- **Mirror budget:** the scanner request never reached `python -`, a scanner note stopped a host, and the mirror's 20 MB log cap blocked databases.
- **Office count:** the office zone count left out idle agents.
- **Caching:** API responses could be served from the browser cache (a stale 410); every API response is now `no-store`.
- **Connections:** several open tabs could exhaust the browser's 6 connections per host; hidden tabs now close their live feed.

Security review (independent, Claude Opus; re-checked after fixes)
- **High:** a session id taken unchecked from agent output (stderr was merged into it) could become a CLI flag on resume (`--dangerously-skip-permissions`).
  - Ids are now validated, the first one wins, they're passed fused (`--resume=<id>`), and stderr is read separately and never parsed.
- **Medium:**
  - the always-ask list could be bypassed (`rm.exe`, `cmd /c del`, `bash -c`, PowerShell `Remove-Item`, `git -c alias`). Interpreters now always ask.
  - a hostile host could flood the mirror. Answers are capped at 2,000 entries and 64 MB, at 20,000 files per host, and at a 30 MB database.
- **Low:**
  - Windows device names, alternate data streams and trailing dots in mirrored paths;
  - the session linker could pick a remote session;
  - a stray control request could end a run.
- **Third pass:** wrappers like `command`, `nohup`, `timeout`, `busybox`, `gh` aliases and `Invoke-Command` still slipped past the always-ask denylist. Run-wide command rules are now limited to an allowlist of read-only commands.
- The 0.5.0 review fixes from earlier the same day were also re-checked.

Known limits
- Ask me works only with Claude Code. Antigravity and opencode headless haven't been probed; Codex headless can't pause.
- Remote runs, the optional relay, and Orchestrate (M5c) aren't in this release.
- Gemini CLI, Kimi and DeepSeek Harness are still hidden until their setup is fixed. Hermes is disabled (`--in` doesn't keep its shell in the project).

## 0.5.0 (2026-11-03): run mode, Dispatch (Milestone 5a)

Added
- **Run mode**, off by default (`config/run.json`). From a Run sheet you can start one-shot agent runs, watch them live on a new Runs page, stop them, and review the diff, then merge or discard it.
  - **Launchers:** eight of them: Claude Code, Codex (always in WSL Debian), Antigravity, opencode, Kimi, Gemini CLI, DeepSeek Harness and Hermes. Each run picks a level (Read-only, Edit project folder, Full auto), mapped to each tool's own flags.
  - **Worktrees:** each editing run gets its own git worktree and branch. You can view the diff, merge exactly the commit you viewed, or discard it. Nothing is pushed.
  - **Project allowlist** (`config/projects.json`).
  - **Account pool:** one config folder per profile.
  - **Quota guard** (Warn, Block and suggest, Auto-route): it uses provider-usage and the stored limit snapshots.
  - **Process control:**
    - Windows: a Job Object, with the child started suspended until it's assigned.
    - Linux: POSIX process groups.
    - WSL: a wrapper that records the run's process group.
    - Also time limits, recovery after a restart, and at most 3 concurrent runs by default.
  - **Session linking:** a run links to the session the readers find, through the stream id in the readers' format or a time-and-project fallback. Tokens and value come from the checked readers. Office figures show "launched by HQ".
  - **Logging:** every run is logged to provider-usage, and a real rate limit marks the profile blocked.
- `config/run.json.example` and `config/projects.json.example`.

Verified on this PC (2026-11-03)
- **Real read-only runs** through the API:
  - **Claude Code:** session linked, and its tokens in Agent HQ equal an independent hand count (104,794).
  - **Codex** (in WSL), **opencode** and **Antigravity:** sessions linked and token totals checked.
  - Every run left the project checkout untouched.
- Kimi, DeepSeek Harness and Gemini CLI fail for setup reasons on this PC and stay hidden. Hermes is disabled (see Known limits).
- **Tests:** 384 Python and 125 web tests pass on Windows (Python 3.14). The runner tests also pass in WSL.

Fixed during the real-tool checks
- **Session ids:** stream session ids now use the readers' `<harness>:<id>` format. Before this, no run joined its session.
- **Rate limits:** they now come only from structured error fields. Before, Claude Code's informational `rate_limit_event`, or an agent merely mentioning "429", would have blocked an account.
- **Antigravity:** `-p` takes the prompt as its value, and its real event stream (`init`, `result`) is now parsed.
- **npm 10 shims** (`opencode.CMD`, `gemini.CMD`, `dsh.CMD`) are resolved to the real program, so no prompt passes through `cmd.exe`.
- **Harness names:** launcher names now match the readers (`claude_code`, `gemini_cli`). Readers that record no project (Gemini CLI, Kimi) link a run only when exactly one session matches.
- **Guard suggestions** stay within the tools the Run sheet offers. Before, it suggested Hermes, which is disabled.
- **Windows environments** keep the non-secret variables tools need to start (`APPDATA`, `PATHEXT`, ...).

Security review (independent, Claude Opus; re-checked after the fixes)
- **High:** an agent could plant a git config (`core.fsmonitor`) in its worktree, and Agent HQ's own diff or merge would run it.
  - Fix: git never finds a repository from inside a worktree. It gets an explicit `--git-dir`/`--work-tree`, hooks and fsmonitor are off, and the empty hooks folder is random.
  - A run that changed its worktree's `.git` is refused.
- **Medium:** an agent that could read the token file could start runs itself.
  - Fix: run routes also need an in-memory run key, handed to the page once at launch.
- **Medium:** output lines and diffs had no size limit.
  - Fix: 64 KB per line and 500 lines kept; diffs are read in a stream up to 2 MB, and a diff of more than 50,000 changed lines is refused.
- **Low:**
  - Merge used a fresh snapshot; it now uses the commit you viewed.
  - Rate limits were matched in free text.
  - Arguments after `wsl --` went through the Linux shell; now `--exec`.
  - Model names could start with `-`.
  - Auto mode could change the tool between confirm and start.
  - A nested repository could crash diff or merge.

Known limits
- **Hermes is disabled.** On this PC its shell commands run in its configured folder (`zl_os`) even with `--in`.
- **Run key exposure:** other programs run by the same user can see the launch link's one-time code for 120 s, and the browser may keep the run key in its session-restore data.
- **Untested launchers:** Kimi, DeepSeek Harness and Gemini CLI haven't passed a real run. The bundled stream samples are invented; the real shapes are pinned in tests.
- **Not in this release:** Steer (follow-ups, approvals), Orchestrate and remote hosts.

## 0.4.0 (2026-11-03): more sources, Milestone 4

Added
- **Our own readers:**
  - **Kimi** (Kimi Code and Kimi CLI), including subagent folders.
  - **DeepSeek Harness**, v4 and v3 logs, including subagents linked through the child's report to its parent.
  - **Zylos**: its comm-bridge messages become events; Claude and Codex sessions under `~/zylos` are relabelled Zylos; message text is off by default.
  - **Antigravity token counts and models**, via a stdlib port of llm-usage-metrics' protobuf decoder.
- **DeepSeek as a provider**, plus a **DeepSeek API balance** card on Limits (opt-in key, 15 min refresh, low-balance alert). This is the app's first outbound request, and it is made only when a key exists.
- **Long-tail importer:** llm-usage-metrics 0.12.1 is installed on request from Settings into a private folder. It imports Copilot CLI, Goose, Amp, Qwen, Cline, Roo, Kilo, Droid, pi and OpenClaw, never the tools read natively. Imported agents have an `active` status and a dotted ring in the office.
- **Bundled LiteLLM prices.** Your `prices.json` wins; Settings shows each model's price source.
- **Reader health** says "untested on this PC" until a reader has been checked against real logs.
- `NOTICE` credits llm-usage-metrics and LiteLLM (MIT).

Fixed during the real-log checks
- **DeepSeek Harness is installed on this PC** (28 sessions), and its v4 format differs from the v3 one upstream parses. Changes to the reader:
  - provider and model come from `request/header`;
  - tool calls, turn ends and real prompts are now read;
  - compaction usage is counted;
  - subagent links come from the parent receiving the child's `agent-message`.

  Before this fix only token events were stored.
- **zstd frame splitter:** frames are walked by the decompressor, not by searching for the magic number, which can appear inside a payload. An incomplete last frame is a torn tail unless a complete frame follows it.
- **Zylos previews:** the switch could never turn on, because readers only receive their own source config.
- **Importer install on Windows:** it failed because `npm` is `npm.cmd`. It now resolves the full path, uses a 300 s install timeout, and reports an `installing` state.

Verified on real logs (2026-11-03), with independent hand-count scripts
- DeepSeek Harness: 28 sessions, 122,756,699 tokens, 972 tool calls, 92 prompts, 42 turn ends, 4 subagents linked. 0 mismatched sessions.
- Antigravity: 40 conversations, 306,072,353 tokens. 0 mismatches, including the largest (77,296,252) and the oldest.
- Kimi: the one real session (setup records only) reads with 0 warnings.
- Importer: installed through `POST /api/importer/install` on Windows (health `installing` → `ok`). `doctor` finds no long-tail tools on this PC, so 0 sessions are imported. The tools read natively (claude, codex, antigravity, dsh) are excluded.
- Full pass: every reader `ok`, 0 warnings.

Independent review (risk gate)
- Reviewer: a fresh Claude Opus agent. 4 medium and 3 low findings, 5 confirmed; all fixed test-first, and the re-check reports 6 fixed and 1 partial (low):
  - The DeepSeek key followed redirects. It now refuses any 3xx, sends the header unredirected, and accepts https only.
  - One bad Zylos row stalled the reader.
  - A damaged mid-log zstd frame stopped every DeepSeek Harness session. The frame is now skipped, and errors are isolated per file (Kimi too).
  - 30 of 31 price aliases didn't resolve. All 29 now resolve, with `provider/` prefixes handled.
  - Provider normalisation overwrote routing providers. It now only fills a missing one.
  - Importer install hardened: one install at a time, `--ignore-scripts`, npm must come from a PATH folder.
  - The importer cursor now re-reads a 1-day overlap, with a stable dedupe key that covers all token fields.
- **Found by the author:**
  - Installing the importer rewrote `config/`. It's now detected from its executable in the data folder instead.
  - Claude's `<synthetic>` placeholder is no longer listed for pricing.
- After the fixes, the real-log hand counts were re-run on a fresh database: DeepSeek Harness and Antigravity still match exactly.

Known limits
- The importer's own npm dependencies are not pinned by a lockfile. Install scripts are disabled.
- An imported event's own `costUsd` is not stored. Imported usage is valued with the price table like everything else.
- Kimi token parsing, Zylos and the importer's mapping are tested only against upstream test logs, because no real data for them exists on this PC.

## 0.3.1 (2026-11-02): Codex history and subagents

Fixed
- **Compressed Codex sessions were skipped.** Codex compresses finished sessions to `.jsonl.zst`, and 593 of 656 session files on this PC were in that form, so Agent HQ showed only about a tenth of Codex history.
  - They are now read with Python 3.14's built-in `compression.zstd`. On older Python they're skipped and counted as warnings in reader health.
  - Unchanged compressed files are not re-read: the cursor is size plus mtime.
- **Codex subagents were merged into their parent.** A subagent file starts with its own `session_meta`, then copies its parent's. The reader treated the copy as a session switch, so every subagent's events went to the parent and no subagent appeared on its own.
  - Now only a file's first `session_meta` defines its session.
  - The subagent's parent comes from `source.subagent.thread_spawn.parent_thread_id`, and its nickname becomes the title.

Verified (2026-11-02, real logs)
- Codex sessions went from 87 to 696, of which 291 are subagents linked to their parent. History now starts on 2026-08-27, not 2026-10-19.
- The largest compressed session spans 2 files (no replayed records between them). A hand count from the raw files matches Agent HQ exactly: 7,882 tool calls, 18,610,957 input, 2,810,623 output and 1,152,305,024 cache-read tokens. Its 133 subagents are now separate children.
- Codex total: 12.07 B tokens, the same as before the subagent fix, now credited to the right sessions.
- First full pass: 35 s, because every compressed file is decompressed once. The next pass took 2.8 s.
- Tests: Python 127 (two skip on Windows); web 100.

## 0.3.0 (2026-11-02): office view, Milestone 3

Added
- **Office page, now the start page:** every agent from the last 24 h is a figure in an office.
  - One tinted zone per harness, plus a break room.
  - The figure's body is coloured by harness. Its state shows as a ring, with a "!" bubble when it needs you and a red puff on error.
  - Idle agents wait in the break room with their desk kept, shown dimmed.
  - Desks stay stable as agents come and go.
  - New subagents and delegations walk between zones, and a ticker shows the latest delegation.
  - Hover shows the title; click opens the inspector.
  - Filters by harness and account, and a hidden agent list for screen readers.
- **Three skins:** Isometric (default), Top-down, and Tower (one floor per harness, with a lift). The choice is remembered; the default comes from Settings.
- **Under the hood:**
  - PixiJS 8, loaded only when the Office opens. All art is drawn in code, with no bitmap assets.
  - A retained scene graph: the static layer is rebuilt only when rooms, size or theme change, and each NPC is one persistent object.
- **API:** `GET /api/delegations` returns the delegation feed (no notes).
- **Dev only:** `?synthetic=N` for performance checks, removed from production builds.

Fixed (found by checking the running office on real data)
- **Walks never showed:** delegations reach the page 3–7 s late, which the first walk rule missed.
- **History animated:** the first scene made every existing subagent walk.
- **Scattered layout:** rooms floated as separate plates. Labels used raw ids and were covered by figures. Harness colours were the same as status colours.
- **Dark mode:** the office floor ignored it.
- **1–2 fps with 60 agents:** every skin rebuilt the whole stage and every text label each frame. Now **100 fps** for all three skins, the display's refresh rate.
- **Tower cut off:** its bottom floor was hidden by page scroll.
- **Duplicate requests:** each refresh sent some requests twice.
- **Node 25+ tests:** a broken global `localStorage` failed the Office tests on Windows.

Independent review (risk gate)
- Reviewer: a fresh Claude Opus agent. Security clean (the delegations endpoint, rendering of titles, nothing leaving the machine). 3 medium, 3 low and 1 minor finding, all fixed test-first:
  - History animated on first load.
  - The canvas didn't follow the stage's size.
  - Tower clicks picked the wrong agent at small scale.
  - Filters reshuffled desks.
  - A refresh could reuse a stale request.
  - Teardown crashed before start, and a failed WebGL start left a blank stage.
  - Very long hover titles.

Verified (2026-11-02)
- Tests: 100 web, 123 Python (two DST tests skip on Windows).
- Real logs, light and dark mode: all three skins showed the right agents in the right zones. Click opened the right session, with no console errors.
- 60 synthetic agents: 100 fps in each skin, against a no-canvas baseline of 100 fps.
- 30 skin switches left 1 canvas, and the JS heap went from 22 MB to 16 MB.
- With the inspector open and closed, the canvas width follows the stage (966 → 666 → 966 px).

Known limits
- With hundreds of agents, Isometric and Top-down get crowded and partly clipped.
- Reduced motion is covered by tests and code review only, because this browser can't emulate the setting.

## 0.2.0 (2026-10-31): web UI, Milestone 2

Added
- React + TypeScript UI served by `python -m hq`:
  - pages: Dashboard, Agents (with the inspector), Limits (with manual entry) and Settings;
  - alert banner and sidebar limit bars;
  - live refresh over one shared SSE stream, reconnecting with backoff;
  - light and dark mode.
- API:
  - `GET /api/series` (hourly or daily tokens, value, tasks and errors per group);
  - session rows carry `last_kind`, `last_tool`, `last_preview` and `tokens`;
  - session events accept `order=desc`;
  - top sessions are listed without prices (ranked by tokens).
- The token file is reused across launches.

Fixed (found by checking the running UI on real data)
- Claude event previews showed Python `repr` text for nested tool results.
- The inspector timeline showed a session's oldest 50 events instead of its newest.
- Every API hook opened its own SSE connection, which would exhaust the browser's 6-per-host limit.
- Charts skipped empty hours and days.
- After a rebuild, a cached `index.html` kept loading the old bundle. `index.html` is now `no-cache` and hashed assets are `immutable`.
- Restarting the server sent open pages to the token screen.

Independent review (risk gate)
- Reviewer: a fresh Claude Opus agent. No XSS and no token leaks found; 1 medium-high, 2 medium and 4 low findings, all fixed:
  - Day buckets split in two on daylight-saving change days, which made week and month charts drop data. Reproduced.
  - Charts showed unpriced series as $0.
  - The reused token stayed in browser history. The launch URL now carries a single-use 120 s code exchanged via `POST /api/exchange`, and `--rotate-token` was added.
  - The inspector's status dot was always idle.
  - Stale data showed briefly on range switch.
  - A bad token made the SSE stream retry silently.
  - Every refresh enriched all sessions.

Verified (2026-10-31, real logs)
- Re-check by the same reviewer: all 7 fixed, no new high or medium. A low follow-up was fixed too: a spent launch link no longer signs out a working tab.
- Known limits:
  - A launch link opened more than 120 s after start shows the token help page. Restart, or use `#token=`.
  - Scripts that POST to the API must send a same-origin `Origin` header.
- Tests: 41 web, 120 Python (on Windows; the two DST tests skip without `tzset` and pass on Linux).
- Launch flow in the browser: the code was exchanged, the URL cleaned to `#/dashboard` with no token in it, and reusing the code returned 403.
- Browser checks:
  - Every page in light mode at 1280–1440 px and in dark mode at 1280 px.
  - No horizontal overflow at 1024 px.
  - No `NaN` or `undefined` text.
  - No console errors.
  - Requests go to `127.0.0.1` only.
  - Without a token the help page shows.
  - A server restart with the page open keeps working.
- The Dashboard week tokens tile matches `GET /api/kpis?range=week` within one refresh cycle on live data (954.5M = 954.5M).

## 0.1.0 (2026-10-31): backend, Milestone 1

Added
- Readers for Claude Code, Codex (Windows and WSL), opencode, Antigravity, Gemini CLI and provider-usage (incl. Hermes runs).
- SQLite store with incremental cursors, session status rules and task derivation.
- KPIs (activity, quality, tokens, API-equivalent value, plan ROI, effective cost, quota use, routing), limit cards with source badges, staleness, forecast and alerts, and the provider-usage `recommend` bridge.
- Local JSON API with SSE, Host/Origin/token checks; `python -m hq` entry point with `--once` health check.

Verified on real logs (2026-10-31)
- One full pass: 6 of 6 readers `ok`, 0 skipped lines, about 19,800 events, 3 s.
- Hand count from raw JSONL (independent script) vs the API, exact match:
  - Claude Code session (afk-desk): 641 tool calls, 303,310,232 tokens.
  - Codex session: 997 tool calls, 137,132,267 tokens.
- Live server: foreign `Host` → 403; `POST` without token → 403.
- Timings on real data: overview ~270 ms, week KPIs ~200 ms, month KPIs grouped by harness ~1 s.

Independent review (risk gate)
- Reviewer: a fresh Claude Opus agent that wrote none of the code. Found 3 high, 2 medium, 2 low; 5 reproduced. All 7 confirmed fixed on re-check.
- High fixes: the API token is no longer served by `GET /` and is required on every `/api/*`; sources cannot be changed over the API, and the tracker path is pinned at startup; the forbidden-path guard checks resolved, normalized paths at open time and directory walks skip forbidden folders; the collector cannot die, and read positions are saved in the same transaction as the data.
- After the fixes, on Windows: 101 tests pass (the DST test skips: no `tzset`), all 6 readers `ok` on real logs, the real `usage.db` hash is unchanged after `/api/recommend`.

Known limits
- On Windows the token file relies on the folder's permissions (`%LOCALAPPDATA%` is per-user); do not point `--db` at a shared folder.
- Antigravity tokens and model are unknown (binary step payloads).
- Gemini CLI logs carry no token counts.
- Claude limits are not in the logs; they come from provider-usage 429 records or manual entry.
- Hawkscan (DAST) not run: no `HAWK_API_KEY`.
