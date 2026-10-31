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

Known limits
- Antigravity tokens and model are unknown (binary step payloads).
- Gemini CLI logs carry no token counts.
- Claude limits are not in the logs; they come from provider-usage 429 records or manual entry.
- Hawkscan (DAST) not run: no `HAWK_API_KEY`.
