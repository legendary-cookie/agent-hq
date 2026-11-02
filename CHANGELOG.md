# Changelog

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
