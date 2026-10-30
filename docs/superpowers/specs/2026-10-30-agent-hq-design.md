# Agent HQ: design spec

Date: 2026-10-30. Status: draft for review.

## 1. Purpose

One local web app to watch every AI coding agent running on this PC, across harnesses, and to see what the whole operation costs and is worth.

Success means:
- At a glance: which agents are working, which need me, which are idle, and on which harness, provider, model and account.
- Per account: how much of each usage window is used, when it resets, and which model is best to use right now.
- KPIs per task, session, day, week and month, including API-equivalent value and ROI per subscription.
- A playful office view (isometric by default, top-down and tower as switchable skins) that shows the same live data.

Non-goals (v1):
- Controlling agents (start, stop, send prompts). The app only reads.
- Remote or phone access. It binds to `127.0.0.1` only.
- Calling provider APIs or scraping web dashboards for limits.
- Multi-user or multi-machine aggregation.

## 2. Architecture

```
log files / DBs ──► collector (Python) ──► hq.db (SQLite) ──► server (Python) ──► browser (React)
                     one adapter per harness                   REST + SSE           Office · Dashboard · Agents · Limits · Settings
```

- **Collector**: runs inside the server process on a background thread. Every 5 s it asks each enabled adapter for new data since its saved cursor (file path + byte offset + mtime, or last row id for SQLite sources) and upserts into `hq.db`.
- **Server**: `http.server` (ThreadingHTTPServer), standard library only. Serves the built UI from `web/dist`, a JSON API, and a Server-Sent Events stream that emits `changed` notices after each collector pass that wrote anything.
- **Web UI**: React + TypeScript + Vite. Charts with Recharts. Office rendered with PixiJS.
- **Runtime**: Python 3.11+, no third-party Python packages. Node is needed only to build the UI. Start with `python -m hq`, which opens `http://127.0.0.1:7420/` in the default browser.

## 3. Repository layout

```
agent-hq/
  hq/
    __main__.py        start collector + server, open browser
    config.py          load/validate config/*.json, resolve paths (~, WSL)
    store.py           schema, migrations, upserts, queries
    collector.py       cursors, scheduling, per-adapter error isolation
    adapters/
      base.py          Adapter protocol + dataclasses (SessionRec, EventRec, LimitRec)
      claude_code.py
      codex.py
      opencode.py
      antigravity.py
      gemini_cli.py
      provider_usage.py
    kpis.py            KPI and value calculations
    limits.py          limit state, staleness, forecasts, recommend bridge
    server.py          routes, SSE, security checks
  config/
    sources.json       which adapters are on, and their paths
    accounts.json      plans, prices, reset days, harness/provider mapping
    prices.json        API price per model per million tokens
  tests/
    fixtures/          small scrubbed sample logs per harness
    test_*.py          unittest
  web/
    src/
      api.ts           typed client + SSE hook
      pages/ Office.tsx Dashboard.tsx Agents.tsx Limits.tsx Settings.tsx
      components/      Sidebar, Pills, Inspector, LimitBar, KpiTile, ...
      office/
        scene.ts       scene model (pure, tested)
        skins/ isometric.ts topdown.ts tower.ts
  README.md  CHANGELOG.md
```

## 4. Data model (`hq.db`)

| Table | Columns |
|---|---|
| `accounts` | `id`, `provider`, `label`, `plan`, `monthly_price_usd`, `billing_day`, `match` (JSON rules mapping harness/provider/model to this account). Loaded from `accounts.json` at start and on edit. |
| `sessions` | `id` (harness-prefixed), `harness`, `provider`, `model`, `account_id`, `project`, `title`, `started_at`, `last_event_at`, `ended_at`, `status`, `parent_id`, `source_ref` |
| `events` | `id`, `session_id`, `ts`, `kind` (`user`, `assistant`, `tool_call`, `tool_result`, `error`, `turn_end`), `tool`, `preview` (≤200 chars), `tokens_in`, `tokens_out`, `tokens_cache_read`, `tokens_cache_write`, `tokens_estimated` (bool), `model` |
| `tasks` | `id`, `session_id`, `started_at`, `ended_at`, `outcome` (`ok`, `error`, `open`). One task = one user/delegator prompt up to the next `turn_end`. Derived by the collector. |
| `delegations` | Mirror of `provider-usage` `calls` (read-only copy): `ts`, `provider`, `model`, `task_kind`, `outcome`, `tokens`, `duration_s`, `note` |
| `limit_snapshots` | `account_id`, `window` (`5h`, `week`, `day`, `month`), `used_percent`, `resets_at`, `source` (`measured`, `blocked`, `manual`), `recorded_at` |
| `cursors` | `adapter`, `key`, `offset`, `mtime` |
| `prices` | `model`, `input`, `output`, `cache_read`, `cache_write` (USD per 1M tokens). Loaded from `prices.json`. |

**Status rules** (computed at query time from `last_event_at` and the last event's kind):
- `working`: an event in the last 60 s and the last event is not `turn_end`.
- `waiting`: the last event is `turn_end` and is less than 10 min old.
- `idle`: no event for 10 min or more, not ended.
- `done`: `ended_at` set, or no event for 24 h.
- `error`: the last event is `error`.

**Account matching**: the first rule in `accounts.json` whose `harness`, `provider` and optional `model` glob match the session. Unmatched sessions go to the account `unassigned` and show a hint in Settings.

## 5. Adapters

Shared rules:
- Each adapter reads only paths listed in `sources.json`. The config loader rejects paths whose segments include `secrets`, `auth.json`, `oauth_creds.json`, `credentials`, `google_accounts.json` or `.env`, so no adapter can read them even if misconfigured.
- SQLite sources open with `mode=ro` URIs and query a fixed list of tables.
- A line or row that fails to parse is skipped with a counted warning. An adapter that throws is disabled for that pass and marked `partial` in the UI; other adapters keep running.

| Adapter | Source | Provides | Limits |
|---|---|---|---|
| `claude_code` | `~/.claude/projects/**/*.jsonl` | sessions, subagents (via `parentUuid`/sidechain links), tool calls, per-message tokens incl. cache, model, cwd | none in logs |
| `codex` | `~/.codex/sessions/**/*.jsonl`, plus the Debian WSL home (`\\wsl$\Debian\home\<user>\.codex\sessions`) when present | sessions, tool calls, tokens, model, cwd | `rate_limits.primary/secondary` → `5h`/`week` snapshots, source `measured` |
| `opencode` | `~/.local/share/opencode/opencode.db` (tables `session`, `message`, `part`, `todo` only) | sessions, messages, tool parts, model/provider per message, tokens when present | none |
| `antigravity` | `~/.gemini/antigravity-cli/conversations`, `history.jsonl` | conversations, steps, model | none; tokens estimated (4 chars ≈ 1 token) and flagged |
| `gemini_cli` | `~/.gemini/tmp/*/chats` | chat sessions, model, tokens when present | none |
| `provider_usage` | `~/.claude/provider-usage/usage.db` (read-only) | `delegations`; Hermes appears only here | `limits` table → `blocked` snapshots (100 % until `blocked_until`) |

Grok and any harness without local logs: accounts and limits entered by hand.

Exact field names for each adapter are confirmed against the real files during implementation and frozen in the test fixtures.

## 6. KPIs and value

All KPIs accept a range (`today`, `week`, `month`, `custom`, local time zone, weeks start Monday) and a `group_by` (`harness`, `provider`, `model`, `account`), and return the previous period for comparison.

| Group | Definition |
|---|---|
| Activity | active agents now; sessions started; tasks; tool calls; agent-hours (sum of session active time, gaps over 10 min excluded); average task duration |
| Quality | task success rate = `ok` / (`ok` + `error`); delegation outcomes by kind; errors per 100 tool calls |
| Tokens | input, output, cache read, cache write; cache hit ratio = cache read / (input + cache read) |
| Value | Σ tokens × `prices` for that model. Unpriced tokens are excluded and reported as a count. Estimated tokens are included and the total is flagged `est.` |
| Plan ROI | per account per billing month: value ÷ `monthly_price_usd` |
| Effective cost | per session: account price × (session tokens ÷ account tokens in that billing month) |
| Quota use | for accounts with `measured` snapshots: average peak `used_percent` per window over the range |
| Routing | delegations per provider; count of `blocked` snapshots; hours blocked per model |

Lists: top sessions by value, top projects by value, most-used models.

## 7. Limits

- One card per account. A bar per window, with `used_percent`, a reset countdown, and a source badge: `Measured`, `Blocked`, `Manual`, `Unknown`.
- A reading older than its window length is shown as `stale`, with its age.
- Forecast: for `measured` windows, a linear fit over the last 30 min of snapshots → "limit in ~Xh Ym". For other accounts, only if a rough `cap_tokens` per window is set in `accounts.json`; otherwise show burn rate (tokens/hour).
- Alerts at 80 %, 95 % and blocked: in-app banner always; Windows toast optional, off by default.
- "Best now": imports `tracker.py` from the provider-usage folder and calls its `recommend` logic. If the import fails, the strip hides and Settings shows why.
- Manual entry: `POST /api/limits/manual` stores a `manual` snapshot. This is the only write path for limits. The app never writes to `usage.db`.

## 8. API

| Method | Path | Returns |
|---|---|---|
| GET | `/api/overview` | status counts, best-now, today's value and tokens, active alerts |
| GET | `/api/sessions?status=&harness=&account=&from=&to=` | session list |
| GET | `/api/sessions/{id}` | session, events (paged), tasks, children, account limit |
| GET | `/api/kpis?range=&from=&to=&group_by=` | KPI groups, current and previous period |
| GET | `/api/limits` | cards per account with windows, sources, staleness, forecasts |
| POST | `/api/limits/manual` | stores a manual snapshot |
| GET | `/api/recommend` | ranked models |
| GET/PUT | `/api/settings` | accounts, prices, sources, alert thresholds, default skin (PUT writes the JSON files after validation) |
| GET | `/api/health` | adapter states, last pass, warnings |
| GET | `/api/stream` | SSE `changed` events |

Security:
- Bind `127.0.0.1` only.
- Reject requests whose `Host` is not `127.0.0.1:<port>` or `localhost:<port>`, which blocks DNS rebinding.
- POST/PUT require a per-launch random token, given to the page at load, and a matching `Origin`.

## 9. UI

Visual direction: minimal and Apple-like, per the approved mockup.
- System font stack (SF Pro / Segoe UI Variable).
- Frosted sidebar and inspector, soft rounded cards, generous whitespace.
- One blue accent; status colours only for status: green working, orange needs you, red blocked/error, grey idle.
- Light and dark mode follow the system. Fits widths down to 1024 px; below that the inspector becomes a sheet.

Pages:
- **Office** (default): status pills, a skin segmented control (Isometric / Top-down / Tower), harness and account filters, the stage, an event toast, and an inspector for the selected agent.
- **Dashboard**: range control, group-by control, KPI tiles, charts (value per day stacked by provider, tokens by model, tasks vs errors), tables (top sessions, plan ROI, blocked hours).
- **Agents**: sortable, filterable session table; the same inspector with timeline and subagent tree.
- **Limits**: account cards as in section 7.
- **Settings**: accounts and plan prices, model prices (unpriced models listed first), sources and adapter health, alert thresholds, default skin.

The sidebar always shows mini limit bars for the 4 most constrained windows.

## 10. Office

**Scene model** (`scene.ts`, pure TypeScript, no rendering):
- Input: sessions and recent events.
- Output: `rooms` (one per harness with active or recent sessions, plus a break room), `desks` (one per session that is not done, stably assigned so agents keep their seat), and `npcs` with `state` (`working`, `waiting`, `idle`, `error`), position target, colour by harness, and child links.
- `walks`: a short animation from parent to child desk when a delegation or subagent starts.

**Skins** implement one interface: `mount(container)`, `render(scene, dt)`, `pick(x, y) → npcId`, `destroy()`.
- **Isometric** (default, built first): one floor split into harness zones.
- **Top-down**: one room per harness, pixel style.
- **Tower**: one floor per harness, a lift for cross-harness walks.

NPC visuals per state:
- Working: typing loop.
- Waiting: "!" bubble.
- Idle: walks to the break room.
- Error: red puff.

Clicking an NPC opens the inspector. Character sprites are drawn as simple original vector/pixel art in code or as small bundled sprite sheets created for this project, never copied from other projects.

The skin choice is saved in `localStorage`. The default comes from Settings.

## 11. Testing

- Python: `unittest`.
  - Adapters are tested against scrubbed fixtures; one test per field we rely on.
  - Store tests cover upserts and cursor resume.
  - KPI tests use hand-computed expected values.
  - Status-rule tests use a fixed clock.
  - Security tests cover Host, Origin and token checks.
  - Config tests check that forbidden paths are rejected.
- Web: Vitest for `scene.ts`, KPI formatting and the API client. One Playwright smoke test loads each page against a server seeded from fixtures.
- Manual: run against the real logs on this PC and check that numbers match a hand count for one Codex and one Claude session.

## 12. Milestones

1. Backend: store, collector, all six adapters, KPIs, limits, API, health. Verified with tests and real logs.
2. UI shell: sidebar, Dashboard, Agents, Limits, Settings.
3. Office: scene model + isometric skin + inspector.
4. Top-down and tower skins.
5. Docs: README (install, run, config), CHANGELOG, Brain vault note.

## 13. Inputs needed from you during build

- Claude plan name and monthly price; any other plans (Grok, NineRouter) to list.
- API prices for models without public prices (e.g. `gpt-6-luna`). They stay unpriced until set.
- Where the repo should live and whether to create a GitHub repo for it.
