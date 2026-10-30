# Agent HQ Backend (Milestone 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local Python service that reads agent logs from six sources into one SQLite database and serves sessions, KPIs and limits over a JSON API with a live SSE stream.

**Architecture:** `hq` is a standard-library Python package. Adapters turn each harness's files into shared records (`SessionRec`, `EventRec`, `LimitRec`, `DelegationRec`). A collector thread polls the adapters every 5 s and upserts into `hq.db`. A `ThreadingHTTPServer` on `127.0.0.1:7420` serves the API. UI, office and skins are later plans.

**Tech Stack:** Python 3.11+ standard library only (`sqlite3`, `json`, `http.server`, `threading`, `unittest`, `importlib`).

**Spec:** `docs/superpowers/specs/2026-10-30-agent-hq-design.md` (sections 1–8, 11, 12.1).

## Global Constraints

- Python 3.11+, no third-party Python packages.
- All timestamps are stored as UTC epoch seconds (`float`). Ranges use the local time zone; weeks start Monday.
- The server binds `127.0.0.1` only, port `7420` by default.
- Every source SQLite DB is opened with `file:...?mode=ro` URIs. Nothing is ever written to `usage.db` or to any harness file.
- The config loader rejects any source path containing a segment, case-insensitive, in: `secrets`, `auth.json`, `oauth_creds.json`, `credentials`, `google_accounts.json`, `.env`.
- Event previews are truncated to 200 characters.
- Prices are USD per 1,000,000 tokens. Unpriced tokens are never valued; their count is reported.
- Token normalization: `tokens_in` excludes cached input; `tokens_cache_read` is the cached input.
- Status thresholds: working < 60 s, waiting < 600 s after `turn_end`, idle ≥ 600 s, done after 86,400 s or when ended.
- Tests: `python -m unittest discover -s tests` from the repo root must pass. No test reads real files outside `tests/fixtures`.
- Fixtures are hand-scrubbed: no real prompts, paths with user names, emails or IDs from the user's accounts.

## Review Focus

1. **A log file that is being appended while it is read** (half-written last line). Expect the partial line to be left for the next pass, never parsed or dropped. Test: `test_tail_partial_line` in Task 3.
2. **A log file that is rotated or truncated** (size smaller than the saved offset). Expect a reread from 0 with no duplicate events. Tests: `test_tail_truncated_restarts` in Task 3, and the dedupe on `(session_id, ext_id)` in Task 2.
3. **A source that is missing or locked** (opencode DB locked by a running opencode, WSL not mounted). Expect that adapter to be marked `partial` with the error text, other adapters unaffected, and the server still up. Test: `test_failing_adapter_isolated` in Task 10.
4. **A model with no price** (`gpt-6-luna`). Expect value to exclude it and report `unpriced_tokens`. Test: `test_value_excludes_unpriced` in Task 11.
5. **A request from another origin or Host** (DNS rebinding, a malicious page POSTing to localhost). Expect 403. Tests: `test_bad_host_rejected`, `test_post_without_token_rejected` in Task 13.

---

### Task 1: Package scaffold and config

**Files:**
- Create: `hq/__init__.py`, `hq/config.py`, `config/sources.json`, `config/accounts.json`, `config/prices.json`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces:
  - `Config` dataclass: `sources: dict[str, SourceCfg]`, `accounts: list[Account]`, `prices: dict[str, Price]`, `alerts: dict` (`{"thresholds": [80, 95], "toast": false}`), `default_skin: str`, `recommend_task_kind: str`.
  - `SourceCfg(enabled: bool, paths: list[Path])`.
  - `Account(id, provider, label, plan, monthly_price_usd: float | None, billing_day: int, match: list[dict], cap_tokens: dict[str, int])`.
  - `Price(input, output, cache_read, cache_write)`.
  - `load_config(config_dir: Path) -> Config`.
  - `save_config(config_dir: Path, cfg: Config) -> None`.
  - `match_account(cfg, harness, provider, model) -> str`, which returns `"unassigned"` when nothing matches.
  - `ForbiddenPathError(ValueError)`.

- [ ] **Step 1: Write the failing tests**

  - `test_expands_home_and_globs`: `"~/.codex/sessions"` resolves under `Path.home()`.
  - `test_forbidden_path_rejected`: for each forbidden segment (e.g. `~/.hermes/secrets/x`, `~/.gemini/oauth_creds.json`, `C:/x/Auth.JSON`), `load_config` raises `ForbiddenPathError`.
  - `test_match_account_first_rule_wins`: with rules `[{harness:"codex"}, {provider:"openai"}]`, `("codex","openai","gpt-6-luna")` → first account.
  - `test_match_account_model_glob`: rule `{harness:"antigravity", model:"gemini-4-argon*"}` matches `gemini-4-argon-high`.
  - `test_unassigned_when_no_rule`.
  - `test_save_roundtrip`: `save_config` then `load_config` returns an equal `Config`.

- [ ] **Step 2: Run** `python -m unittest tests.test_config -v`. Expected: FAIL with `ModuleNotFoundError: hq.config`.

- [ ] **Step 3: Implement `hq/config.py`**

  Path expansion uses `os.path.expanduser`. Glob patterns stay as strings until the adapter walks them. The forbidden check runs on `Path(p).parts`, lower-cased.

  Seed the config files:
  - `sources.json`: enable all six adapters with the paths from spec section 5. The WSL Codex path is `\\wsl$\Debian\home\*\.codex\sessions`. `provider_usage.paths = ["~/.claude/provider-usage"]`.
  - `accounts.json`, five accounts:
    - `claude` (provider `anthropic`, price `null`, match `{harness:"claude_code"}`)
    - `chatgpt-plus` (`openai`, 20, `{harness:"codex"}`)
    - `antigravity-pro` (`google`, price `null`, `{harness:"antigravity"}`, `{harness:"gemini_cli"}`)
    - `opencode-free` (`opencode`, 0, `{harness:"opencode"}`)
    - `ninerouter` (`ninerouter`, 0, `{provider:"ninerouter"}`, `{harness:"hermes"}`)
  - `prices.json`: `{}`. The user fills it in. No invented prices.

- [ ] **Step 4: Run** the tests. Expected: PASS.

- [ ] **Step 5: Commit** with `git commit -m "feat: config loading with forbidden-path guard"`.

### Task 2: Store

**Files:**
- Create: `hq/store.py`, `hq/records.py`, `hq/status.py`
- Test: `tests/test_store.py`, `tests/test_status.py`

**Interfaces:**
- Produces, in `hq/records.py` (frozen dataclasses):
  - `SessionRec(id: str, harness: str, provider: str, model: str | None, project: str | None, title: str | None, started_at: float, last_event_at: float, ended_at: float | None, parent_id: str | None, source_ref: str)`
  - `EventRec(session_id: str, ext_id: str, ts: float, kind: Literal["user","assistant","tool_call","tool_result","error","turn_end"], tool: str | None, preview: str | None, tokens_in: int | None, tokens_out: int | None, tokens_cache_read: int | None, tokens_cache_write: int | None, tokens_estimated: bool, model: str | None)`
  - `LimitRec(harness: str, provider: str, scope: str, window: Literal["5h","week","day","month","block"], used_percent: float, resets_at: float | None, source: Literal["measured","blocked","manual"], recorded_at: float)`. `scope` is a model name or `"*"`.
  - `DelegationRec(ext_id: str, ts: float, provider: str, model: str, task_kind: str, outcome: str, tokens: int | None, duration_s: float | None, note: str | None)`
  - `Batch(sessions: list, events: list, limits: list, delegations: list, warnings: int = 0)`
- Produces, in `hq/store.py`:
  - `Store(path: Path)` with methods:
    - `apply(batch: Batch, account_of: Callable[[str,str,str|None], str]) -> bool`: True if anything changed.
    - `get_cursor(adapter, key) -> tuple[int, float] | None`.
    - `set_cursor(adapter, key, offset, mtime)`.
    - `derive_tasks(session_ids: Iterable[str])`.
    - `conn()`, a new read connection per thread.
  - Schema as spec section 4, with `limit_snapshots.scope` added and `UNIQUE(session_id, ext_id)` on `events`.
- Produces, in `hq/status.py`: `session_status(last_event_at, last_kind, ended_at, now) -> str`.

- [ ] **Step 1: Write the failing tests**
  - `test_apply_inserts_and_is_idempotent`: applying the same batch twice leaves 1 session and N events, and the second `apply` returns `False`.
  - `test_session_upsert_extends_last_event`: a later batch with a newer `last_event_at` updates it; an older one does not move it back.
  - `test_account_assigned_on_insert`: `account_of` is called and its result is stored.
  - `test_cursor_roundtrip`.
  - `test_derive_tasks`: events `user@0, tool_call@5, turn_end@9, user@20, error@25` give tasks `(0,9,"ok")` and `(20,25,"error")`. A trailing `user` with no end gives `outcome "open"`.
  - `test_status_rules`, with now = 1000:
    - `(990,"assistant",None)` → working
    - `(990,"turn_end",None)` → waiting
    - `(300,"assistant",None)` → idle
    - `(1000-86400-1,"assistant",None)` → done
    - `(999,"assistant",995)` → done
    - `(999,"error",None)` → error

- [ ] **Step 2: Run** `python -m unittest tests.test_store tests.test_status -v`. Expected: FAIL.

- [ ] **Step 3: Implement.** Use one writer connection owned by the collector, with WAL mode and `INSERT ... ON CONFLICT DO UPDATE`. `last_event_at = max(old, new)`.

- [ ] **Step 4: Run** the tests. Expected: PASS.

- [ ] **Step 5: Commit** with `"feat: sqlite store, records, status rules"`.

### Task 3: Incremental file tail and adapter base

**Files:**
- Create: `hq/adapters/__init__.py`, `hq/adapters/base.py`, `hq/tail.py`
- Test: `tests/test_tail.py`

**Interfaces:**
- Consumes: `Store.get_cursor/set_cursor` (Task 2).
- Produces:
  - `read_new_lines(path: Path, cursor: tuple[int,float] | None) -> tuple[list[str], tuple[int,float]]`. Returns only complete lines after the cursor offset. If the file size is smaller than the offset, it restarts from 0.
  - `Adapter` protocol: `name: str`, `poll(store: Store, cfg: SourceCfg) -> Batch`.
  - `iter_files(cfg: SourceCfg, pattern: str) -> Iterator[Path]`.
  - `ro_connect(path: Path) -> sqlite3.Connection`.
  - `preview(text: str | None) -> str | None`, which truncates to 200 characters and collapses whitespace.

- [ ] **Step 1: Write the failing tests**
  - `test_tail_reads_complete_lines`.
  - `test_tail_partial_line`: a file ending `{"a":1}\n{"b":` returns 1 line, and the cursor sits at the end of the first line. After appending `2}\n`, the next call returns `{"b":2}`.
  - `test_tail_truncated_restarts`.
  - `test_preview_truncates_to_200`.
  - `test_ro_connect_cannot_write`: an INSERT raises `sqlite3.OperationalError`.

- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement.** Open the file in binary mode, split on `b"\n"`, and decode with `utf-8` and `errors="replace"`.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** with `"feat: incremental tail reader and adapter base"`.

### Task 4: Claude Code adapter

**Files:**
- Create: `hq/adapters/claude_code.py`, `tests/fixtures/claude/<proj>/<sid>.jsonl`, `tests/fixtures/claude/<proj>/<sid>/subagents/<aid>.jsonl`
- Test: `tests/test_adapter_claude.py`

**Interfaces:**
- Consumes: Task 3 base and `records`.
- Produces: `ClaudeCodeAdapter` (`name = "claude_code"`).

**Source facts** (observed 2026-10-30):
- Each JSONL line has a `type`. Relevant types are `user`, `assistant`, `system`, `custom-title`, `agent-name`.
- `user`/`assistant` lines carry `sessionId`, `uuid`, `timestamp` (ISO), `cwd`, `isSidechain`, and `message`.
- `assistant.message` has `model`, `content[]` (blocks of type `text`, `tool_use` with `name`, and `thinking`), `stop_reason`, and `usage` (`input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `output_tokens`).
- `user.message.content[]` can include `tool_result` blocks with `is_error`.
- `custom-title.customTitle` is the session title.
- Subagent transcripts live in `<sessionId>/subagents/*.jsonl`.

**Mapping:**
- The session id is `claude_code:<sessionId>`. A subagent session id is `claude_code:<sessionId>:<file stem>`, with `parent_id` set to the main session.
- `provider = "anthropic"`, `project = cwd`.
- Each `tool_use` block becomes a `tool_call` event (`tool = name`, `ext_id = uuid#i`).
- A `tool_result` with `is_error` becomes an `error` event.
- An assistant line with `stop_reason == "end_turn"` emits a `turn_end` event after its own event.
- Tokens are recorded on the assistant event: `in = input_tokens`, `cache_read = cache_read_input_tokens`, `cache_write = cache_creation_input_tokens`, `out = output_tokens`.
- Files whose path contains `subagents` produce the subagent session.

- [ ] **Step 1: Write the fixture and failing tests**

  The fixture is a 6-line scrubbed session plus a 2-line subagent file.
  - `test_session_fields`: harness, provider, model `"claude-opus-5-5"`, title from `custom-title`, project.
  - `test_tool_call_and_error_events`.
  - `test_tokens_mapped`: the fixture assistant usage is `{input_tokens:2, cache_creation_input_tokens:3992, cache_read_input_tokens:183880, output_tokens:389}`, which gives `(2, 389, 183880, 3992)`.
  - `test_turn_end_emitted`.
  - `test_subagent_parent_link`.
  - `test_incremental_second_poll_empty`.
  - `test_garbage_line_counted_as_warning`.

- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement `poll`.** Iterate files with `iter_files(cfg, "**/*.jsonl")` and read them with `read_new_lines`.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** with `"feat: claude code adapter"`.

### Task 5: Codex adapter

**Files:**
- Create: `hq/adapters/codex.py`, `tests/fixtures/codex/2026/10/30/rollout-x.jsonl`
- Test: `tests/test_adapter_codex.py`

**Interfaces:** produces `CodexAdapter` (`name = "codex"`).

**Source facts:** each line is `{type, timestamp, payload}`.
- `session_meta.payload` has `id`, `cwd`, `model_provider`, `timestamp`.
- `turn_context.payload` has `model`, `cwd`.
- `event_msg` with `payload.type` of:
  - `task_started`
  - `task_complete`, which maps to `turn_end`
  - `token_count`: `info.last_token_usage{input_tokens, cached_input_tokens, cache_write_input_tokens, output_tokens}` and `rate_limits{primary{used_percent, window_minutes, resets_at}, secondary{...}}`
  - `item_completed`
- `response_item` with `payload.type` of `custom_tool_call`, `function_call` (`name`), `message` (`role`, `content`), and `custom_tool_call_output`.

**Mapping:**
- The session id is `codex:<session_meta.id>`; `provider = "openai"`.
- Tokens are attached as a separate `assistant` event per `token_count` (`ext_id = "tc#<line offset>"`): `in = input_tokens - cached_input_tokens`, `cache_read = cached_input_tokens`, `cache_write = cache_write_input_tokens`.
- Rate limits: `window_minutes == 300` gives `"5h"`, `10080` gives `"week"`, and any other value gives `"day"` if ≤ 1440, else `"month"`. Use `source = "measured"`, `scope = "*"`, `resets_at` as given, and `recorded_at` = the line timestamp.

- [ ] **Step 1: Write the fixture and failing tests**
  - `test_session_from_meta_and_model_from_turn_context`.
  - `test_token_normalization`: input 26222 and cached 12672 give in 13550 and cache_read 12672.
  - `test_rate_limits_to_snapshots`: primary 6.0/300 gives `("5h", 6.0, 1793218194)`; secondary 2.0/10080 gives `"week"`.
  - `test_task_complete_is_turn_end`.
  - `test_tool_call_named`.
- [ ] **Step 2–4:** run (FAIL), implement, run (PASS). Skip a missing WSL path without error.
- [ ] **Step 5: Commit** with `"feat: codex adapter with measured limits"`.

### Task 6: opencode adapter

**Files:**
- Create: `hq/adapters/opencode.py`, plus `tests/fixtures/opencode/make_fixture.py`, which builds `opencode.db` in a temp dir with the 4 tables.
- Test: `tests/test_adapter_opencode.py`

**Interfaces:** produces `OpencodeAdapter` (`name = "opencode"`).

**Source facts:**
- `session(id, parent_id, directory, title, model, time_created, time_updated, time_archived, ...)`. Times are milliseconds.
- `message(id, session_id, time_created, data JSON{role, modelID, providerID, tokens{input, output, reasoning, cache{read, write}}, finish, time})`.
- `part(id, message_id, session_id, time_created, data JSON{type: "tool"|"text"|"step-finish"|..., tool?, state?{status}})`.

**Mapping:**
- The session id is `opencode:<id>`, with `parent_id` mapped. `provider` and `model` come from the latest assistant message.
- A message becomes a `user`/`assistant` event carrying tokens (`out = output + reasoning`).
- A `tool` part becomes `tool_call`. A part whose `state.status == "error"` becomes `error`. An assistant message with a non-null `finish` emits `turn_end`.
- The cursor is `max(time_updated)` per table, held in `Store` cursors with key = table name.
- Only `session`, `message`, `part` and `todo` are queried; the SQL is fixed strings.

- [ ] **Step 1: Write the failing tests**
  - `test_reads_sessions_messages_parts`.
  - `test_tokens_from_message_json`: `{input:20303, output:3, cache:{read:0, write:0}}` gives `(20303, 3, 0, 0)`; provider `"ninerouter"`, model `"kr/auto"`.
  - `test_only_whitelisted_tables_queried`: an `sqlite3` trace callback records every statement, and every statement must reference only the four tables.
  - `test_locked_db_raises_cleanly`: an exclusive lock held by another connection makes `poll` raise `sqlite3.OperationalError`. The collector handles it (Task 10).
- [ ] **Step 2–4:** run (FAIL), implement, run (PASS).
- [ ] **Step 5: Commit** with `"feat: opencode adapter"`.

### Task 7: Antigravity adapter

**Files:**
- Create: `hq/adapters/antigravity.py`, `tests/fixtures/antigravity/history.jsonl`, `tests/fixtures/antigravity/conversations/<id>.db` (built by a helper as in Task 6)
- Test: `tests/test_adapter_antigravity.py`

**Interfaces:** produces `AntigravityAdapter` (`name = "antigravity"`).

**Source facts:**
- `history.jsonl` lines are `{display, timestamp (ms), workspace, conversationId}`; one line per user prompt.
- `conversations/<id>.db` has a `steps` table (`idx`, `step_type`, `status`, protobuf blobs). The blobs are **not parsed**.

**Mapping:**
- The session id is `antigravity:<conversationId>`, `provider = "google"`, `model = None`, and `project = workspace`.
- Each history line becomes a `user` event with `preview = display`.
- Activity: `last_event_at = max(history ts, conversation db file mtime)`.
- A step-count increase since the last poll emits one `assistant` event at the db mtime, with `ext_id = "steps#<count>"`.
- All token fields are `None` and `tokens_estimated = False`. The UI shows "unknown".

- [ ] **Step 1: Write the failing tests**
  - `test_history_creates_sessions_and_user_events`.
  - `test_step_growth_emits_activity`.
  - `test_tokens_unknown`.
  - `test_missing_conversation_db_ok`.
- [ ] **Step 2–4:** run (FAIL), implement, run (PASS).
- [ ] **Step 5: Commit** with `"feat: antigravity adapter (activity only)"`.

### Task 8: Gemini CLI adapter

**Files:**
- Create: `hq/adapters/gemini_cli.py`, `tests/fixtures/gemini/tmp/proj/chats/session-x.jsonl`
- Test: `tests/test_adapter_gemini.py`

**Interfaces:** produces `GeminiCliAdapter` (`name = "gemini_cli"`).

**Source facts:**
- Line 1 is a header `{sessionId, projectHash, startTime, lastUpdated, kind}`.
- Later lines are `{"$set": {"messages": [...]}}` snapshots. Each message has `id`, `timestamp`, `type` (`user`/`gemini`/...), `content`, and optional `model`/`tokens`.

**Mapping:**
- The session id is `gemini_cli:<file stem>`; `provider = "google"`.
- Each message `id` becomes one event (`ext_id = id`), deduped by the store. Tokens are taken when present, else `None`.

- [ ] **Step 1: Write the failing tests**
  - `test_header_and_messages`.
  - `test_repeated_set_snapshots_do_not_duplicate`.
  - `test_tokens_when_present`.
- [ ] **Step 2–4:** run (FAIL), implement, run (PASS).
- [ ] **Step 5: Commit** with `"feat: gemini cli adapter"`.

### Task 9: provider-usage adapter

**Files:**
- Create: `hq/adapters/provider_usage.py`, plus `tests/fixtures/pu/make_fixture.py`, which uses the exact `calls`/`limits` schema from `tracker.py`.
- Test: `tests/test_adapter_pu.py`

**Interfaces:** produces `ProviderUsageAdapter` (`name = "provider_usage"`).

**Mapping:**
- Each `calls` row becomes a `DelegationRec` (`ext_id = id`, `ts` parsed from ISO).
- Each `limits` row whose `blocked_until` is in the future becomes a `LimitRec` with `harness = provider`, `scope = model`, `window = "block"`, `used_percent = 100`, `resets_at = blocked_until`, `source = "blocked"`, and `recorded_at = recorded_ts`.
- Hermes calls (`provider == "hermes"`) also produce a synthetic session `hermes:<call id>` with `started_at = ts - duration_s`, one `assistant` event carrying `tokens`, and a `turn_end` event.

- [ ] **Step 1: Write the failing tests**
  - `test_calls_to_delegations`.
  - `test_active_block_to_limit`.
  - `test_expired_block_ignored`.
  - `test_hermes_call_becomes_session`.
  - `test_never_writes`: the source file's mtime and bytes are unchanged after `poll`.
- [ ] **Step 2–4:** run (FAIL), implement, run (PASS).
- [ ] **Step 5: Commit** with `"feat: provider-usage adapter"`.

### Task 10: Collector

**Files:**
- Create: `hq/collector.py`
- Test: `tests/test_collector.py`

**Interfaces:**
- Consumes: `Store`, `Config`, `match_account`, all adapters.
- Produces:
  - `Collector(store, cfg, adapters: list[Adapter], interval: float = 5.0)` with methods `run_once() -> bool`, `start()`, `stop()`, `health() -> dict[str, dict]`, and `on_change(callback)`.
  - `health()` returns, per adapter: `{state: "ok"|"partial"|"disabled", last_pass: float, warnings: int, error: str|None}`.

- [ ] **Step 1: Write the failing tests**
  - `test_run_once_applies_batches_and_derives_tasks`.
  - `test_failing_adapter_isolated`: one adapter raises `OSError("locked")`. The others still apply, its health is `partial` with error `"locked"`, and it is retried on the next pass.
  - `test_on_change_called_only_when_changed`.
  - `test_disabled_adapter_skipped`.
- [ ] **Step 2–4:** run (FAIL), implement, run (PASS). Use a daemon thread with an `Event`-based stop.
- [ ] **Step 5: Commit** with `"feat: collector loop with per-adapter isolation"`.

### Task 11: KPIs and value

**Files:**
- Create: `hq/kpis.py`
- Test: `tests/test_kpis.py`

**Interfaces:**
- Consumes: the `Store` schema and `Config.prices`/`accounts`.
- Produces:
  - `resolve_range(name: Literal["today","week","month","custom"], now: datetime, start=None, end=None) -> Range(start, end, prev_start, prev_end)`.
  - `value_usd(tokens_in, tokens_out, cache_read, cache_write, price: Price | None) -> float | None`.
  - `compute_kpis(conn, cfg, rng: Range, group_by: str | None, now: float) -> dict`, with keys `activity`, `quality`, `tokens`, `value`, `plan_roi`, `quota_use`, `routing`, `top_sessions`, `top_projects`, `top_models`, and `previous` (same shape, minus the lists).
  - `session_value(conn, cfg, session_id) -> dict`, with keys `tokens`, `value_usd`, `unpriced_tokens`, `effective_cost_usd`, `estimated`.

- [ ] **Step 1: Write the failing tests.** Seed a temp store by hand. Use a fixed `now` of `2026-10-30T12:00` local.
  - `test_resolve_week_starts_monday`: Thu Oct 29 gives a start of Mon Oct 26 00:00 local and a previous week starting Mon Oct 19.
  - `test_value_usd_basic`: price `{input:3, output:15, cache_read:0.3, cache_write:3.75}`, tokens `(1_000_000, 100_000, 2_000_000, 0)` gives `3 + 1.5 + 0.6 = 5.1`.
  - `test_value_excludes_unpriced`: two sessions, one on an unpriced model, give `value.total_usd` for the priced one only and `value.unpriced_tokens > 0`.
  - `test_success_rate`: 3 ok and 1 error give 0.75.
  - `test_cache_hit_ratio`: in 100, cache_read 300 gives 0.75.
  - `test_agent_hours_excludes_gaps`: events at 0, 60, 120 and 2000 s give 120 s, because the gap over 600 s is excluded.
  - `test_plan_roi`: the account price is 20 and the value in its billing month is 143.0, giving `roi = 7.15`. An account with price `null` gives `roi = None`.
  - `test_effective_cost_split_by_tokens`: an account at 20 with sessions of 1k and 3k tokens gives 5.0 and 15.0.
  - `test_quota_use_average_peak`: two measured `5h` windows peaking at 60 and 90 give `quota_use["chatgpt-plus"]["5h"] = 75.0`.
  - `test_group_by_provider`.
  - `test_routing_counts_blocks_and_hours`.
- [ ] **Step 2–4:** run (FAIL), implement in SQL plus small Python aggregation, run (PASS).
- [ ] **Step 5: Commit** with `"feat: KPI and value calculations"`.

### Task 12: Limits

**Files:**
- Create: `hq/limits.py`
- Test: `tests/test_limits.py`

**Interfaces:**
- Consumes: `limit_snapshots`, `Config.accounts`, `match_account`, `kpis` token sums.
- Produces:
  - `WINDOW_SECONDS = {"5h": 18000, "week": 604800, "day": 86400, "month": 2592000}`.
  - `limit_cards(conn, cfg, now) -> list[dict]`. Each card has `account_id`, `label`, and `windows: [{window, scope, used_percent | None, resets_at, source | "unknown", stale: bool, age_s, forecast_s | None, burn_tokens_per_h}]`.
  - `alerts(cards, thresholds) -> list[dict]`.
  - `forecast_seconds(points: list[tuple[float,float]], now) -> float | None`.
  - `add_manual(store, account_id, window, used_percent, resets_at, now)`.
  - `recommend_bridge(pu_dir: Path, task_kind: str) -> list[dict] | None`.

- [ ] **Step 1: Write the failing tests**
  - `test_latest_snapshot_per_window`.
  - `test_stale_when_older_than_window`: a `5h` snapshot 18001 s old is stale.
  - `test_forecast_linear`: points `(now-1800, 50)`, `(now, 80)` give `forecast_s = 1200`. Fewer than 2 points, or a flat or negative slope, give `None`.
  - `test_blocked_counts_as_100_until_reset`.
  - `test_unknown_when_no_data`.
  - `test_alert_levels`: 79 gives none, 80 gives warn, 95 gives critical, blocked gives blocked.
  - `test_manual_entry_validated`: `used_percent` outside 0–100 raises `ValueError`.
  - `test_recommend_bridge_import_failure_returns_none`: a nonexistent dir gives `None`, with no exception.
- [ ] **Step 2–4:** run (FAIL), implement, run (PASS).

  `recommend_bridge` loads `tracker.py` with `importlib.util.spec_from_file_location` and calls `recommend(task_kind)`. It returns `[r for r in result]` converted to dicts, or `None` on any exception.
- [ ] **Step 5: Commit** with `"feat: limit cards, staleness, forecast, alerts"`.

### Task 13: HTTP server and API

**Files:**
- Create: `hq/server.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `Store`, `Collector`, `kpis`, `limits`, `config`.
- Produces:
  - `make_server(store, collector, cfg, config_dir, port=7420, static_dir: Path | None) -> (ThreadingHTTPServer, token: str)`.
  - Routes exactly as spec section 8. `GET /` serves `static_dir/index.html` with the line `<meta name="hq-token" content="{token}">` injected, or a plain "UI not built yet" page when `static_dir` is missing.
  - SSE: `GET /api/stream` sends `event: changed\ndata: {"ts":...}\n\n` on collector change, plus a `: ping` comment every 15 s.

- [ ] **Step 1: Write the failing tests.** Start the server on port 0 in a thread with a seeded store.
  - `test_overview_shape`.
  - `test_sessions_filter_by_status`.
  - `test_session_detail_includes_children_and_value`.
  - `test_kpis_week`.
  - `test_limits_cards`.
  - `test_health`.
  - `test_bad_host_rejected`: `Host: evil.com` gives 403.
  - `test_post_without_token_rejected`: a missing `X-HQ-Token` gives 403, and a wrong `Origin` gives 403.
  - `test_manual_limit_post_ok`.
  - `test_settings_put_rejects_forbidden_path`: 400.
  - `test_sse_receives_changed`.
  - `test_unknown_route_404_json`.
- [ ] **Step 2–4:** run (FAIL), implement, run (PASS). JSON errors look like `{"error": "..."}` and never include stack traces.
- [ ] **Step 5: Commit** with `"feat: local JSON API with SSE and host/origin/token checks"`.

### Task 14: Entry point, docs, real-log verification

**Files:**
- Create: `hq/__main__.py`, `README.md`, `CHANGELOG.md`
- Test: `tests/test_main.py`

**Interfaces:**
- `python -m hq [--port 7420] [--config DIR] [--db PATH] [--no-browser] [--once]`.
- The default `--db` is `%LOCALAPPDATA%/agent-hq/hq.db` (`~/.local/share/agent-hq/hq.db` on other platforms).
- `--once` runs one collector pass, prints a per-adapter health table, and exits 0. Any adapter in `partial` gives exit 1.

- [ ] **Step 1: Write the failing test.** `test_once_with_fixture_config_exits_0` runs `python -m hq --once --config tests/fixtures/config --db <tmp>` in a subprocess and asserts exit 0 and that the output lists all six adapters.
- [ ] **Step 2–4:** run (FAIL), implement, run (PASS).
- [ ] **Step 5: Real-log check.** Run `python -m hq --once` against the real config. Then:
  - Pick one Codex session and one Claude session.
  - Hand-count their tool calls and sum their tokens straight from the JSONL (a one-off script in the scratchpad, not committed).
  - Compare with `GET /api/sessions/{id}`.
  - Record both numbers in the CHANGELOG entry. Any mismatch blocks this task.
- [ ] **Step 6: Docs.**
  - README: what it is, requirements, `python -m hq`, the config files, and the privacy rules (allowlist, forbidden paths, read-only).
  - CHANGELOG: `0.1.0` with the verification numbers.
- [ ] **Step 7: Commit** with `"feat: entry point, docs, verified against real logs"`.
