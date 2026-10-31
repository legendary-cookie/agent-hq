# Changelog

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
