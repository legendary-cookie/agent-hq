# Agent HQ Web UI (Milestone 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A minimal, Apple-like web UI served by `python -m hq`, with these pages:
- Dashboard: KPIs and charts.
- Agents: session table with an inspector.
- Limits: account cards with manual entry.
- Settings: plans, prices, alerts, sources and health.

All pages update live over SSE.

**Architecture:**
- A React + TypeScript single-page app in `web/`, built by Vite into `web/dist`. The Python server already serves that folder at `/`.
- The page takes the per-launch token from the URL fragment and sends it on every API call.
- A few backend additions feed the charts and the inspector.
- The office view is Milestone 3. In this milestone the Office nav item is shown disabled ("Soon"), and the default route is Dashboard.

**Tech Stack:**
- Python 3.11 stdlib (backend additions).
- Node 20+ (26 installed).
- Front end, versions pinned exactly in `package.json`: react 18.3.1, react-dom 18.3.1, vite 5.4.11, @vitejs/plugin-react 4.3.4, typescript 5.6.3, recharts 2.13.3.
- Types: @types/react 18.3.12, @types/react-dom 18.3.1.
- Tests: vitest 2.1.8, jsdom 25.0.1, @testing-library/react 16.1.0, @testing-library/jest-dom 6.6.3.

**Spec:** `docs/superpowers/specs/2026-10-30-agent-hq-design.md` (sections 6, 7, 8, 9). The visual target is `docs/design/mockup-v2-office.html`, approved by the user: open it in a browser before building any page.

## Global Constraints

**Visual style**
- Apple-like minimal: system font stack `-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI Variable", "Segoe UI", system-ui, sans-serif`.
- Frosted sidebar and inspector (`backdrop-filter: blur(20px)`), card radius 14–18 px, generous whitespace.
- One accent `#0071e3`. Status colours are used only for status: working `#34c759`, needs you/waiting `#ff9f0a`, blocked/error `#ff3b30`, idle/done `#aeaeb2`.
- Light and dark mode follow `prefers-color-scheme`. Colours live as CSS variables in `web/src/theme.css`.
- Layout works from 1024 px wide. Below 1024 px the inspector becomes a bottom sheet.

**Data display**
- Never show `NaN`, `undefined` or `$0.00` for unknown values. Unknown is shown as `—`. Unpriced value is shown as "unpriced" with the token count. Estimated totals carry an `est.` suffix.

**Security**
- The token lives in `sessionStorage` key `hq-token`, never `localStorage`, never in logs.
- Every `/api/*` call sends header `X-HQ-Token`. The SSE URL uses `?token=`.
- No third-party network requests at runtime: no CDN fonts, no analytics.

**Dependencies and build**
- Only the npm packages listed under Tech Stack. No CSS framework, no router library, no state library.
- `web/dist` stays git-ignored. The README tells the user to build it.

**Tests**
- Python: `python -m unittest discover -s tests`.
- Web: `npm --prefix web test` (vitest run).
- Both must pass at the end of every task.

## Review Focus

1. **Opened without a token, or with an old token after a restart.** The API returns 403. Expect a full-page message ("Open Agent HQ from the link printed when it starts, or from the `token` file beside hq.db"), not a blank or broken page. Test: `shows token help on 403` in Task 3.
2. **Unknown values** (`roi: null`, `success_rate: null`, `monthly_price_usd: null`, a limit window with `window: null`). Expect `—` or "unpriced", never NaN or $0. Tests: `formats null as dash` and `formats unpriced value` in Task 4, `KpiTile shows dash for null` in Task 5.
3. **Huge numbers** (303,310,232 tokens; 997 tool calls). Expect compact formatting (`303.3M`) with no table overflow at 1024 px. Test: `compact token format` in Task 4. Visual check in Task 10.
4. **Server restarted while the page is open** (the SSE connection drops). Expect automatic reconnect with backoff, then a refetch. Test: `reconnects SSE and refetches` in Task 3.
5. **Empty data** (no sessions in range, an account with no limit data). Expect a calm empty state per section, not an empty table or an error. Test: `Agents empty state` in Task 7, `Limits unknown window` in Task 8.

---

### Task 1: Backend additions for the UI

**Files:**
- Modify: `hq/kpis.py`, `hq/server.py`
- Test: `tests/test_ui_api.py`

**Interfaces:**
- Produces `series(conn, cfg, rng: Range, bucket: Literal["hour","day"], by: Literal["harness","provider","model","account"]) -> list[dict]` in `hq/kpis.py`.
  - Each row is `{"t": float (bucket start, local time, epoch s), "key": str, "tokens": int, "value_usd": float | None, "tasks": int, "errors": int}`.
  - `value_usd` is `None` when every token in the row is unpriced.
  - Buckets use local midnight (day) or local hour starts. Rows exist only where there is data.
- Produces `GET /api/series?range=&from=&to=&bucket=day&by=provider`, with the same token and Host rules as the other routes.
- Changes `GET /api/sessions`: each row gains `last_kind`, `last_tool` (tool of the newest `tool_call` event or `None`), `last_preview` (newest non-null preview or `None`) and `tokens` (sum of all four token columns). It accepts `limit` (default 500, max 2000).
- Changes `compute_kpis(...)["top_sessions"]`: rows become `{"session_id", "tokens", "value_usd": float | None}`, sorted by `value_usd` descending (None last), then by tokens, top 20. Unpriced sessions are still listed. `top_projects` uses the same rule.

- [ ] **Step 1: Write the failing tests** in `tests/test_ui_api.py`, using a seeded temp store as in `tests/test_kpis.py`. Close every Store and connection.
  - `test_series_by_provider_per_day`: events on two local days for providers a and b give 4 rows with the correct tokens and tasks.
  - `test_series_value_none_when_unpriced`.
  - `test_series_errors_counted`.
  - `test_sessions_rows_have_last_tool_and_tokens`.
  - `test_top_sessions_listed_without_prices`.
  - `test_series_requires_token`: 403 without a token.
- [ ] **Step 2:** `python -m unittest tests.test_ui_api -v` → FAIL.
- [ ] **Step 3:** Implement. Last tool and preview come from one query per request using `ROW_NUMBER()` partitioned by session, as in `hq/status.py:last_kinds`.
- [ ] **Step 4:** Full Python suite → PASS.
- [ ] **Step 5:** Commit `feat(api): series endpoint, richer session rows, top sessions by tokens`.

### Task 2: Web scaffold, theme, shell and token bootstrap

**Files:**
- Create: `web/package.json`, `web/package-lock.json` (from `npm install`), `web/tsconfig.json`, `web/vite.config.ts`, `web/index.html`, `web/src/main.tsx`, `web/src/theme.css`, `web/src/app/App.tsx`, `web/src/app/router.ts`, `web/src/app/token.ts`, `web/src/components/Sidebar.tsx`, `web/src/test/setup.ts`
- Test: `web/src/app/token.test.ts`, `web/src/app/router.test.ts`

**Interfaces:**
- `token.ts`: `bootstrapToken(loc: Location, storage: Storage): string | null`.
  - It reads `#token=<t>` from the hash, saves it to `storage['hq-token']`, and replaces the hash with `#/dashboard` via `history.replaceState`.
  - With no token in the hash, it returns the stored token or null.
- `router.ts`: `type Route = "dashboard" | "agents" | "limits" | "settings"`, `parseRoute(hash: string): Route` (unknown → "dashboard"), and `useRoute(): [Route, (r: Route) => void]` (listens to `hashchange`).
- `vite.config.ts`:
  - `base: "/"`, `build.outDir: "dist"`.
  - Dev proxy `/api` → `http://127.0.0.1:7420` with `changeOrigin: true`, and headers forcing `origin: "http://127.0.0.1:7420"` so POST/PUT pass the server's Origin check in dev.
  - Vitest `environment: "jsdom"`, `setupFiles: ["src/test/setup.ts"]`.
- `package.json` scripts: `dev`, `build` (`tsc --noEmit && vite build`), `test` (`vitest run`).

**Steps:**
- [ ] Write the failing tests:
  - `stores token from hash and rewrites hash`.
  - `falls back to stored token`.
  - `unknown route is dashboard`.
- [ ] Run `npm --prefix web test` → FAIL.
- [ ] Implement.
  - `App.tsx` renders the sidebar and the active page, with a placeholder per page for now.
  - The sidebar matches the mockup: brand "Agent HQ"; nav items Office (disabled, "Soon"), Dashboard, Agents, Limits; a separator; a slot for mini limit bars (filled in Task 5); Settings at the bottom.
  - `theme.css` defines the variables for both colour schemes.
- [ ] Run the tests → PASS. Run `npm --prefix web run build` → `web/dist/index.html` exists.
- [ ] Commit `feat(web): scaffold, theme, shell, token bootstrap`.

### Task 3: API client, types, live updates

**Files:**
- Create: `web/src/api/types.ts`, `web/src/api/client.ts`, `web/src/api/live.ts`, `web/src/app/TokenHelp.tsx`
- Test: `web/src/api/client.test.ts`, `web/src/api/live.test.ts`

**Interfaces:**
- `types.ts`: TypeScript types for every response shape in Task 1 and spec section 8: `Overview`, `SessionRow`, `SessionDetail`, `Kpis`, `KpiBlock`, `LimitCard`, `LimitWindow`, `Recommendation`, `Settings`, `Health`, `SeriesRow`. They mirror the JSON exactly, with `| null` wherever the server can send null.
- `client.ts`:
  - `class ApiError extends Error { status: number }`.
  - `api<T>(path: string, init?: RequestInit): Promise<T>` adds `X-HQ-Token` and, for non-GET, `Content-Type: application/json`. It throws `ApiError` on non-2xx.
  - `useApi<T>(path: string | null): { data: T | null; error: ApiError | null; loading: boolean; reload(): void }`.
- `live.ts`:
  - `subscribeChanges(onChange: () => void): () => void` opens `EventSource('/api/stream?token=…')`.
  - Change notices are debounced 1000 ms.
  - On error it closes and reconnects with backoff of 1 s, 2 s, 4 s, then a 15 s maximum. On each successful reconnect it calls `onChange` once.
  - `useApi` refetches on every change notice.
- `TokenHelp.tsx`: the full-page message for Review Focus 1, shown by `App` when any request ends in 403.

**Steps:**
- [ ] Write the failing tests, with mocked `fetch` and `EventSource`:
  - `sends token header`.
  - `throws ApiError with status`.
  - `debounces change notices`.
  - `reconnects SSE and refetches`.
  - `shows token help on 403` (render `App` with fetch returning 403).
- [ ] FAIL, implement, PASS.
- [ ] Commit `feat(web): typed API client with live refresh`.

### Task 4: Formatting

**Files:**
- Create: `web/src/lib/format.ts`
- Test: `web/src/lib/format.test.ts`

**Interfaces:**
- `fmtTokens(n: number | null): string`: `—` for null; `999`, `1.2k`, `41.2k`, `3.1M`, `303.3M`, `1.2B`.
- `fmtUsd(v: number | null, opts?: { unpricedTokens?: number; estimated?: boolean }): string`:
  - null or undefined gives `—`.
  - A value with `unpricedTokens > 0` and `v === 0` gives `unpriced`.
  - Otherwise `$41.20`, `$0.62`, `$1.2k`, with an ` est.` suffix when estimated.
- `fmtPct(v: number | null): string`: a ratio 0..1 gives `75%`. `fmtPercentValue(v: number | null)` takes 0..100.
- `fmtDuration(seconds: number | null): string`: `—`, `45 s`, `12 min`, `1 h 48 min`, `3 d 2 h`.
- `fmtRelative(ts: number | null, now: number): string`: `just now`, `5 min ago`, `in 1 h 48 min`.
- `fmtDelta(cur: number | null, prev: number | null): { text: string; dir: "up" | "down" | "flat" | "none" }`: `none` when either side is null or prev is 0. The text looks like `+12%`.

**Steps:**
- [ ] Write the failing tests with exactly the examples above:
  - `compact token format` must cover 303310232 → `303.3M`.
  - `formats null as dash`.
  - `formats unpriced value`.
- [ ] FAIL, implement, PASS.
- [ ] Commit `feat(web): formatting helpers`.

### Task 5: Shared components and sidebar limits

**Files:**
- Create: `web/src/components/{LimitBar,Pill,KpiTile,Segmented,EmptyState,Inspector}.tsx`, `web/src/components/components.css`
- Modify: `web/src/components/Sidebar.tsx`
- Test: `web/src/components/components.test.tsx`

**Interfaces:**
- `LimitBar({ percent: number | null, source: string, stale: boolean })`:
  - Fill colour is green below 80, orange from 80 to below 95, red at 95 or above, red for `blocked`, grey track for null or unknown.
  - Stale bars render at 50% opacity.
- `Pill({ color?: string, children })`.
- `KpiTile({ label, value: string, delta?: ReturnType<typeof fmtDelta> })`. The caller passes an already-formatted value, so `—` shows as is.
- `Segmented<T extends string>({ options: {value: T; label: string}[]; value: T; onChange })`, the macOS-style segmented control from the mockup.
- `EmptyState({ title, hint? })`.
- `AlertBanner({ alerts: Overview["alerts"] })`, rendered by `App` above every page from `/api/overview`. This is spec section 7: an in-app banner, always on.
  - It shows the single most severe alert: blocked, then critical (95%), then warn (80%), with a "+N more" link to Limits.
  - Copy looks like "Codex · 5 hours at 82%" or "Antigravity · gemini-4-argon-medium blocked until Sun 17:49".
  - It can be dismissed until that alert's level changes.
  - Nothing renders when there are no alerts.
- `Inspector({ sessionId: string | null, onClose })`. It loads `/api/sessions/{id}?limit=50` and shows, top to bottom:
  - title (harness name), with "model · account" underneath;
  - a "Now" line: status dot plus `last_tool` or `last_preview`;
  - grouped rows: Project, Started by (parent title or id), Duration, Tokens, Value, Effective cost;
  - the account limit bars;
  - a Timeline list of events (time, kind icon, tool or preview);
  - a Subagents list, where clicking an item opens that session.
- `selectSidebarLimits(cards: LimitCard[], n = 4): {label: string; window: string; percent: number | null; source: string; stale: boolean}[]`. It returns the most constrained windows: blocked first, then by `used_percent` descending, skipping unknown windows. It's exported for the test.

**Steps:**
- [ ] Write the failing tests:
  - `LimitBar colour thresholds` (79/80/95/blocked/null).
  - `KpiTile shows dash for null`.
  - `selectSidebarLimits orders blocked first`.
  - `AlertBanner shows most severe and hides when empty`.
  - `Inspector renders timeline and subagents` (mocked API).
- [ ] FAIL, implement, PASS.
- [ ] Commit `feat(web): shared components, inspector, sidebar limits`.

### Task 6: Dashboard page

**Files:**
- Create: `web/src/pages/Dashboard.tsx`, `web/src/pages/pages.css`
- Test: `web/src/pages/Dashboard.test.tsx`

**Layout,** top to bottom, per spec section 9:
- **Header row:** title "Dashboard". Segmented range control: Today / Week / Month. Group-by segmented control: Harness / Provider / Model / Account.
- **Pills row from `/api/overview`:**
  - working / needs you / idle counts;
  - "Best now: provider · model" from `best_now[0]`, hidden when null;
  - today's value and tokens.
- **KPI tiles from `/api/kpis?range=`** (each with a delta against `previous`):
  - Active agents
  - Tasks
  - Success rate
  - Tool calls
  - Agent hours
  - Tokens
  - API value
  - Cache hit
- **Charts (Recharts, using theme colours):**
  - (a) Stacked area of value per day by the group key, from `/api/series?by=<group>`. It falls back to tokens per day when every row has `value_usd` null, and the title then says "Tokens".
  - (b) Horizontal bar of tokens by model, the top 8 of `top_models`.
  - (c) Bars of tasks against errors per day.
- **Tables:**
  - Top sessions: name, harness, tokens, value. Clicking a row opens the Inspector.
  - Plan ROI: account, price, value, ROI ×, where `roi: null` shows `—`.
  - Routing: delegations per provider, blocked hours per model.

**Steps:**
- [ ] Write the failing test `Dashboard renders tiles, falls back to tokens chart when unpriced, ROI dash`, with mocked API data built from the real shapes in Task 1 (fixture JSON in `web/src/test/fixtures/`).
- [ ] FAIL, implement, PASS.
- [ ] Commit `feat(web): dashboard page`.

### Task 7: Agents page

**Files:**
- Create: `web/src/pages/Agents.tsx`
- Test: `web/src/pages/Agents.test.tsx`

**Layout:**
- Filter pills for status (All, Working, Needs you, Idle, Done, Error) and a harness select.
- A table with columns: status dot, Title (or the id tail), Harness, Model, Account, Project (last path segment, full path in the `title` attribute), Last activity (relative), Tokens.
- Sortable by Last activity (the default) and by Tokens.
- Clicking a row opens the `Inspector` on the right, or as a bottom sheet below 1024 px. Children render indented under their parent when the parent is visible.

**Steps:**
- [ ] Write the failing tests:
  - `Agents filters by status`.
  - `Agents empty state`.
  - `Agents nests children under parent`.
- [ ] FAIL, implement, PASS.
- [ ] Commit `feat(web): agents page`.

### Task 8: Limits page

**Files:**
- Create: `web/src/pages/Limits.tsx`
- Test: `web/src/pages/Limits.test.tsx`

**Layout:** one card per account from `/api/limits`. Each window gets a row:
- window label (`5 hours`, `Week`, `Day`, `Month`, `Blocked · <scope>`);
- `LimitBar`;
- percentage;
- reset countdown;
- source badge (Measured / Blocked / Manual / Unknown);
- `stale · 3 h old` when stale;
- the forecast "limit in ~1 h 40 min" when `forecast_s`, otherwise burn "12k tokens/h".

A "Set manually" disclosure on each card has fields for window, % used and reset time (optional `datetime-local`). It sends `POST /api/limits/manual` with `{account_id, window, used_percent, resets_at}`. Validation: 0..100. The server's 400 message is shown inline.

**Steps:**
- [ ] Write the failing tests:
  - `Limits unknown window` shows "Unknown", no bar fill, no NaN.
  - `Limits manual entry posts and refreshes`.
  - `Limits shows server validation error`.
- [ ] FAIL, implement, PASS.
- [ ] Commit `feat(web): limits page with manual entry`.

### Task 9: Settings page

**Files:**
- Create: `web/src/pages/Settings.tsx`
- Test: `web/src/pages/Settings.test.tsx`

**Sections:**
1. **Plans:** editable table of accounts with label, plan, monthly price (empty = unknown), billing day.
2. **Model prices:**
   - Unpriced models are listed first. Use the models from `top_models` of `/api/kpis?range=month` that are missing from `prices`, each with four inputs.
   - Then the priced models, editable.
3. **Alerts:** thresholds and the toast toggle.
4. **Default office skin:** select (isometric / topdown / tower).
5. **Sources:** read-only paths with their health state from `/api/health`. Note: "Edit config/sources.json and restart to change sources."

Save sends `PUT /api/settings` with the full settings object (sources unchanged). Show the server's 400 message inline, and a quiet "Saved" toast on 200.

**Steps:**
- [ ] Write the failing tests:
  - `Settings lists unpriced models first`.
  - `Settings save sends unchanged sources`.
  - `Settings shows 400 message`.
- [ ] FAIL, implement, PASS.
- [ ] Commit `feat(web): settings page`.

### Task 10: Integrate, verify on real data, document

**Files:**
- Modify: `README.md` (a "Web UI" section: `npm --prefix web ci`, `npm --prefix web run build`, then `python -m hq`), `CHANGELOG.md` (0.2.0), `tests/test_server.py` if needed
- Test: manual checks below; both suites

**Steps:**
- [ ] `npm --prefix web ci && npm --prefix web run build`, then run both test suites. Expected: all pass.
- [ ] Run `python -m hq --db <real db copy>` from PowerShell. In a browser, open the printed URL, then:
  - Check every page in light and in dark mode at 1440 px and at 1024 px widths.
  - Confirm no NaN or `undefined` text anywhere: `document.body.innerText` must not match `/NaN|undefined/`.
  - Confirm no console errors.
  - Confirm no requests to hosts other than 127.0.0.1.
  - Confirm that opening `/` without `#token` shows the token help page.
  - Confirm that restarting the server while the page is open recovers within 15 s.
- [ ] Compare the Dashboard week tokens tile with `GET /api/kpis?range=week` → `tokens` sum. They must match.
- [ ] Docs, CHANGELOG 0.2.0 with the checks above, commit `feat: web UI (milestone 2)`.
