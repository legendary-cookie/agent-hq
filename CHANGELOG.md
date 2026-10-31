# Changelog

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
- Tests: 40 web, 120 Python (on Windows; the two DST tests skip without `tzset` and pass on Linux).
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
