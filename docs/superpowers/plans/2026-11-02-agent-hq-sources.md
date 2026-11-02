# Agent HQ M4: More Sources Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Read Kimi, DeepSeek Harness, Zylos and Antigravity tokens in full detail; show DeepSeek as a provider with its API balance; import the long tail of tools through llm-usage-metrics; and value most models with a bundled LiteLLM price table.

**Architecture:**
- **New readers** follow the existing adapter protocol (`poll(store, cfg) -> Batch`, with cursors carried in `Batch.cursors`).
- **Shared groundwork comes first** (Task 1): store schema, provider normalisation, relabel rules, health `tested`, the zstd frame splitter. This lets Tasks 2–8 run in parallel on separate files.
- **The UI changes** come last (Task 9), against the API shapes fixed in Task 1.

**Tech Stack:** Python 3.11+ stdlib (`compression.zstd` on 3.14+, `urllib.request`, `subprocess`); the existing React/TypeScript UI; llm-usage-metrics **0.12.1** (Node, optional, installed on request).

**Spec:** `docs/superpowers/specs/2026-11-02-agent-hq-sources-design.md` (and `2026-10-30-agent-hq-design.md` for everything unchanged).

## Global Constraints

- **Read-only:** every new reader opens SQLite with `ro_connect`, checks `is_forbidden(path)` before every open, and never writes to a source.
- **Python:** stdlib only. Where zstd is needed, use `compression.zstd`. On Python < 3.14, `.zst`/`.zstd` files are skipped and counted in `warnings`.
- **Network:** the only outbound request is `GET https://api.deepseek.com/user/balance`, with a 10 s timeout, made only when a DeepSeek key exists.
- **DeepSeek key:** read from env `DEEPSEEK_API_KEY`, else the file `<db folder>/secrets/deepseek_api_key`. It is never logged, returned by any API, stored in `config/`, or written to git.
- **Importer subprocess:**
  - executable `<db folder>/tools/node_modules/.bin/llm-usage` (`.cmd` on Windows);
  - `shell=False`, a fixed argument list, a 60 s timeout;
  - environment copied from `os.environ` minus keys matching `KEY|TOKEN|SECRET|PASSWORD` (case-insensitive).
- **llm-usage-metrics:** version exactly `0.12.1`.
- **Excluded imported sources** (read natively): `claude, codex, opencode, gemini, antigravity, kimi, dsh`.
- **Imported status:** active under 600 s, idle under 86400 s, done after.
- **Zylos message previews:** off by default (`zylos_previews: false` in settings).
- **Harness colours (web):** kimi `#6f6af8`, dsh `#4d6bfe`, zylos `#e0598b`, imported sources `#8e8e93`.
- **Labels:** Kimi, DeepSeek Harness, Zylos, Copilot CLI, Goose, Amp, Qwen, Cline, Roo Code, Kilo Code, Droid, pi, OpenClaw.
- **Tests:** `python -m unittest discover -s tests` and `npm --prefix web test` pass after every task. Fixtures copied from llm-usage-metrics or zylos-core carry a one-line provenance comment and are credited in `NOTICE`.

## Review Focus

1. **A torn last zstd frame** (DeepSeek Harness is mid-flush). Expect complete frames to be read and the torn tail skipped without error, then picked up on the next pass. Test: `test_torn_tail_frame_skipped` (Task 1, zstd frames).
2. **The same usage seen twice:**
   - a Kimi CLI `message_id` with several StatusUpdates;
   - an Antigravity `response_id` repeated across rows;
   - an import re-run over the same window.

   Expect each to count once. Tests: `test_status_updates_keep_newest_per_message` (Task 2), `test_repeated_response_id_counted_once` (Task 5), `test_reimport_is_idempotent` (Task 8).
3. **Node, the importer, or the network missing or failing.** Expect that source's health to be `partial` with a readable reason, and every other reader unaffected. Tests: `test_missing_tool_marks_partial` (Task 8), `test_balance_http_error_marks_partial_without_key_in_message` (Task 7).
4. **An imported tool that is also read natively** (e.g. `doctor` reports `codex`). Expect it never to be imported, so nothing is double counted. Test: `test_native_sources_excluded` (Task 8).
5. **Zylos chat text.** Expect no message content in events unless `zylos_previews` is true. Test: `test_previews_off_by_default` (Task 4).

---

### Task 1: Groundwork (schema, providers, relabel, health, zstd frames, settings)

**Files:**
- Create: `hq/zstd_frames.py`, `hq/providers.py`, `NOTICE`
- Modify: `hq/store.py`, `hq/collector.py`, `hq/config.py`, `hq/server.py`, `config/sources.json`
- Test: `tests/test_m4_groundwork.py`

**Interfaces:**
- **Produces, `hq/zstd_frames.py`:**
  - `available: bool`, true when `compression.zstd` imports.
  - `iter_frames(data: bytes) -> Iterator[bytes]` yields each complete frame's decompressed bytes. It splits on the zstd magic `b'\x28\xb5\x2f\xfd'` and skips a frame that fails to decode, but only when it is the last one (a torn tail). An earlier broken frame raises `ValueError`.
  - `read_text(path: Path) -> str` concatenates the frames, decoded as UTF-8 with `errors='replace'`.
- **Produces, `hq/providers.py`:** `normalize_provider(provider: str | None, model: str | None) -> str | None`. It returns `'deepseek'` when the model or provider contains `deepseek` (case-insensitive), `'moonshot'` for `kimi`/`moonshot`, and the given provider otherwise.
- **Produces, store schema** (in `_SCHEMA`, with `ALTER TABLE` guarded by `pragma table_info` for existing DBs):
  - `sessions.imported INTEGER DEFAULT 0`;
  - table `balances(account_id TEXT, currency TEXT, total REAL, granted REAL, topped_up REAL, recorded_at REAL, PRIMARY KEY(account_id, currency, recorded_at))`.
  - `SessionRec` gains `imported: bool = False`, defaulted, so existing callers are unchanged.
- **Produces, relabel rules:**
  - `Config.relabel: list[tuple[Path, str]]`, loaded from `sources.json` key `"relabel": [{"under": "~/zylos", "harness": "zylos"}]`.
  - `Store.apply` replaces `session.harness` with the rule's harness when `session.project` resolves under `under`.
  - The provider is normalised with `normalize_provider` on every apply.
- **Produces, health:**
  - `Collector.health()[name]['tested']` is `'real' | 'upstream'`, from the adapter attribute `tested = 'real'` (default) or `'upstream'`.
  - A new top-level key `'importer'` is filled in by Task 8 and starts as `{'state': 'disabled', ...}`.
- **Produces, settings:**
  - `Config.zylos_previews: bool = False` and `Config.deepseek_alert: float = 5.0`, validated in PUT `/api/settings` (bool; number ≥ 0) and returned by GET.
  - `sources.json` gains disabled-by-default entries: `kimi`, `dsh`, `zylos`, `deepseek_balance`, `importer`. Each is enabled automatically only when its path exists (kimi, dsh, zylos) or its key exists (deepseek_balance). The importer is enabled only after install.
- **Produces, `NOTICE`:** credits llm-usage-metrics (MIT, © the llm-usage-metrics authors; Antigravity decoder, test logs, model map) and LiteLLM (MIT; pricing data).

**Steps:**
- [ ] Write the failing tests:
  - `test_iter_frames_concatenated`: two compressed frames decode to `b'a\n' + b'b\n'`.
  - `test_torn_tail_frame_skipped`: two frames plus half of a third decode to the first two, with no exception.
  - `test_broken_middle_frame_raises`.
  - `test_normalize_provider`: `('opencode', 'deepseek-v4.1-flash-free')` gives `'deepseek'`, `(None, 'kimi-k2')` gives `'moonshot'`, `('openai', 'gpt-6')` gives `'openai'`.
  - `test_relabel_under_zylos`: a claude_code session with project `~/zylos/workspace` is stored with harness `zylos`.
  - `test_imported_column_and_balances_table_exist_after_migration`: open an old-schema DB and confirm both.
  - `test_health_reports_tested`.
  - `test_settings_zylos_previews_validated`: `"yes"` gives 400; `true` saves.
  - Tests needing zstd skip when `available` is false.
- [ ] Run `python -m unittest tests.test_m4_groundwork -v`. Expected: FAIL.
- [ ] Implement.
- [ ] Run the full suite. Expected: PASS.
- [ ] Commit `feat(m4): groundwork for new sources (schema, providers, relabel, health, zstd frames)`.

### Task 2: Kimi reader

**Files:**
- Create: `hq/adapters/kimi.py`, plus `tests/fixtures/kimi/` (from llm-usage-metrics' kimi tests at v0.12.1, both formats, and a copy of the real empty session's 4 setup lines with ids replaced)
- Test: `tests/test_adapter_kimi.py`

**Interfaces:**
- **Consumes:** Task 1's `Batch.cursors` and `SessionRec`.
- **Produces:** `KimiAdapter` (`name = 'kimi'`, `tested = 'upstream'`), with default paths `~/.kimi-code/sessions`, `~/.kimi/sessions`.

**Format:**
- **Kimi Code** writes `sessions/<ws>/<session>/agents/<agent>/wire.jsonl`.
  - Usage lines have `type == 'usage.record'` and `usageScope == 'turn'`, carrying `usage.{inputOther, output, inputCacheRead, inputCacheCreation}`, plus `time` and `model`.
  - The agent dir `main` is the session itself. Any other agent is a subagent session `kimi:<session>:<agent>`, with its parent set to `kimi:<session>`.
- **Kimi CLI** writes `sessions/<group>/<uuid>/wire.jsonl`.
  - Usage is in `message.type == 'StatusUpdate'`, with `message.payload.token_usage.{input_other, output, input_cache_read, input_cache_creation}`, keyed by `payload.message_id`.
  - Keep only the newest per `message_id`: the event id is the `message_id`, so a newer status update replaces the older one in place through the store's token upsert.
- **Both:** provider `'moonshot'`. Tokens map to in = inputOther, out = output, cache_read = inputCacheRead, cache_write = inputCacheCreation.

**Steps:**
- [ ] Write the failing tests:
  - `test_kimi_code_usage_record`;
  - `test_subagent_dir_links_parent`;
  - `test_status_updates_keep_newest_per_message`;
  - `test_empty_real_session_no_warnings`: the 4 setup lines give 1 session, 0 events and 0 warnings;
  - `test_incremental`.
- [ ] FAIL, implement, PASS.
- [ ] Commit `feat(m4): kimi reader`.

### Task 3: DeepSeek Harness reader

**Files:**
- Create: `hq/adapters/dsh.py`, plus `tests/fixtures/dsh/` (from llm-usage-metrics' dsh tests at v0.12.1)
- Test: `tests/test_adapter_dsh.py`

**Interfaces:**
- **Consumes:** Task 1's `zstd_frames.read_text`.
- **Produces:** `DshAdapter` (`name = 'dsh'`, `tested = 'upstream'`).
  - Root: `$DSH_HOME/sessions` (a blank value counts as unset) or `~/.dsh/sessions`.
  - Files: `session*.jsonl` and `*.jsonl.zstd`. Compressed files are read whole, with the cursor = (size, mtime).

**Records:**
- `type == 'session'` gives `id`.
- `type == 'session/title-llm-request'` gives `data.route.provider` and `data.route.model`.
- `type == 'assistant/message'` with `data.usage.{inputTokens, outputTokens, reasoningTokens, cacheReadTokens, cacheWriteTokens}` gives one `assistant` event: out = outputTokens + reasoningTokens.
- `data.toolCalls[].name` gives `tool_call` events.
- The session is `dsh:<id>`, and the provider is normalised (so DeepSeek routes show as `deepseek`).

**Steps:**
- [ ] Write the failing tests:
  - `test_plain_and_zstd_logs_equal`;
  - `test_usage_mapping`;
  - `test_route_sets_provider_model`;
  - `test_torn_tail_read_on_next_pass`: append a frame, then poll again for the new event.
- [ ] FAIL, implement, PASS.
- [ ] Commit `feat(m4): deepseek harness reader`.

### Task 4: Zylos reader

**Files:**
- Create: `hq/adapters/zylos.py`, plus `tests/fixtures/zylos/make_fixture.py` (builds `c4.db` from zylos-core `skills/comm-bridge/init-db.sql`, and the `timestamp` column from `c4-v0.9.0-v1.sql`, copied with provenance)
- Test: `tests/test_adapter_zylos.py`

**Interfaces:**
- **Consumes:** Task 1's relabel rule `{"under": "~/zylos", "harness": "zylos"}`, so Claude and Codex sessions under `~/zylos` become `zylos`. That needs no code in this task.
- **Produces:** `ZylosAdapter` (`name = 'zylos'`, `tested = 'upstream'`), path `~/zylos/comm-bridge/c4.db`.
  - It reads `conversations(id, direction, channel, content, status, timestamp)`, with the cursor on `id`.
  - Session: `zylos:comm`, titled "Zylos messages", with `provider` None and project `~/zylos`.
  - Each row becomes an event with `ext_id = 'c4:<id>'`, kind `user` for `in` and `assistant` for `out`, `tool = channel`, and a timestamp parsed from `'YYYY-MM-DD HH:MM:SS'` as UTC.
  - `preview` is the content (cut to 200 characters) **only when** `cfg.zylos_previews` is true; otherwise None.
  - A failed delivery (`status == 'failed'`) becomes an extra `error` event.

**Steps:**
- [ ] Write the failing tests:
  - `test_messages_become_events`;
  - `test_previews_off_by_default`: no event has a preview;
  - `test_previews_on_when_enabled`;
  - `test_failed_delivery_is_error`;
  - `test_cursor_resumes`.
- [ ] FAIL, implement, PASS.
- [ ] Commit `feat(m4): zylos reader`.

### Task 5: Antigravity tokens

**Files:**
- Create: `hq/protobuf_lite.py`
- Modify: `hq/adapters/antigravity.py`
- Test: `tests/test_protobuf_lite.py`, `tests/test_adapter_antigravity.py`

**Interfaces:**
- **Produces, `hq/protobuf_lite.py`:**
  - `read_fields(data: bytes) -> dict[int, list[int | bytes]]` handles wire types 0 (varint; values over 2**53 are skipped), 2 (bytes), and 1 and 5 (skipped). It raises `ValueError` on truncation.
  - `first_int(fields, n) -> int | None` and `first_bytes(fields, n) -> bytes | None`.
- **Field map** (port of llm-usage-metrics `antigravity-row-parser.ts`, MIT):
  - Table `gen_metadata.data`: field 1 is the chat-model message.
  - Inside the chat-model message:
    - field 4 = usage: input 2, total output 3, cache read 5, visible output 9, reasoning 10, response id 11;
    - field 19 = response model string;
    - field 9 = timestamp wrapper, then field 4, then {seconds 1, nanos 2}.
- **Adapter change:** for each conversation DB whose mtime changed, read `gen_metadata` rows and emit one `assistant` event per unique response id (`ext_id = 'resp:<id>'`), with in = input, out = total output, cache_read = cache read, the model, and the timestamp. Session `model` = newest response model. Set `tested = 'real'` only after Task 10's hand count passes; until then `'upstream'`.

**Steps:**
- [ ] Write the failing tests:
  - `test_varint_and_bytes`;
  - `test_truncated_raises`;
  - `test_usage_from_gen_metadata` (fixture blob built with a small test encoder);
  - `test_repeated_response_id_counted_once`;
  - `test_model_from_field_19`.
- [ ] FAIL, implement, PASS.
- [ ] Commit `feat(m4): antigravity token counts`.

### Task 6: Prices

**Files:**
- Create: `hq/pricebook.py`, `tools/update_prices.py`, `config/prices.litellm.json`
- Modify: `hq/config.py`, `hq/kpis.py` (the 4 `cfg.prices.get(...)` call sites), `hq/server.py`
- Test: `tests/test_pricebook.py`

**Interfaces:**
- **Produces:**
  - `PriceBook(user: dict[str, Price], litellm: dict[str, Price], aliases: dict[str, str])` with:
    - `get(model: str | None) -> Price | None`, looking up user first, then the exact LiteLLM name, then the alias LiteLLM name;
    - `source(model) -> 'yours' | 'litellm' | 'unpriced'`.
  - `Config.pricebook: PriceBook`, built in `load_config`.
  - `Config.prices` stays the user's dict, which is what PUT saves.
  - `kpis.py` uses `cfg.pricebook.get(...)` everywhere it used `cfg.prices.get(...)`.
  - `GET /api/settings` adds `price_sources: {model: source}` for every model in `top_models` of the current month.
- **`tools/update_prices.py`:**
  - Downloads llm-usage-metrics **v0.12.1** `packages/core/src/pricing/data/litellm-pricing-snapshot.json` and `packages/core/src/pricing/litellm-model-map.json` (GitHub raw URLs at the tag).
  - Converts per-token USD to per-1M, keyed by model, and writes `config/prices.litellm.json` as `{"source": "...@v0.12.1", "models": {...}, "aliases": {...}}`.
  - Run once; commit the output. It is never run at app runtime.

**Steps:**
- [ ] Write the failing tests:
  - `test_user_price_wins`;
  - `test_litellm_exact`;
  - `test_alias_lookup`;
  - `test_unknown_unpriced`;
  - `test_kpis_value_uses_litellm_when_no_user_price`.
- [ ] FAIL, implement, PASS.
- [ ] Run `python tools/update_prices.py` and commit the JSON.
- [ ] Commit `feat(m4): bundled LiteLLM prices with user overrides`.

### Task 7: DeepSeek balance

**Files:**
- Create: `hq/adapters/deepseek_balance.py`
- Modify: `hq/limits.py`, `hq/server.py` (`/api/limits` includes balances)
- Test: `tests/test_deepseek_balance.py`

**Interfaces:**
- **Produces:** `DeepseekBalanceAdapter` (`name = 'deepseek_balance'`) with `poll()`. It requests at most once per 900 s, tracked in the instance, and calls `fetch_balance(key: str, url: str = 'https://api.deepseek.com/user/balance', timeout: float = 10) -> list[dict]`.
- **Response:** `{"balance_infos": [{"currency", "total_balance", "granted_balance", "topped_up_balance"}]}`, stored in `balances` (account_id `'deepseek-api'`).
- **Limits:** `limit_cards` adds a card `{"account_id": "deepseek-api", "label": "DeepSeek API", "balance": {"currency", "total", "granted", "topped_up", "recorded_at"}, "windows": []}`. Alerts add level `'low_balance'` when `total < cfg.deepseek_alert`.
- **Key loading:** `load_key(db_dir: Path) -> str | None` (env, then file). An error message never contains the key.

**Steps:**
- [ ] Write the failing tests, against a local `http.server` fake:
  - `test_balance_stored_and_card_shown`;
  - `test_no_key_no_request`;
  - `test_rate_limited_to_15_min`;
  - `test_low_balance_alert`;
  - `test_balance_http_error_marks_partial_without_key_in_message`.
- [ ] FAIL, implement, PASS.
- [ ] Commit `feat(m4): deepseek api balance`.

### Task 8: Long-tail importer

**Files:**
- Create: `hq/adapters/importer.py`
- Modify: `hq/server.py` (`POST /api/importer/install`), `hq/status.py` (`session_status(..., imported=False)`), and its callers in `hq/server.py`/`hq/kpis.py`
- Test: `tests/test_importer.py`, with a fake `llm-usage` executable (a Python script, plus a `.cmd` wrapper on Windows) that prints canned `doctor --json` and `events` output

**Interfaces:**
- **Produces:**
  - `ImporterAdapter(tools_dir: Path)` (`name = 'importer'`, `tested = 'upstream'`).
  - `install(tools_dir) -> None` runs `npm install --prefix <tools_dir> llm-usage-metrics@0.12.1`, with the same subprocess rules, and enables the source.
  - `POST /api/importer/install` (token + Origin) calls it in a background thread and returns `{"started": true}`.
- **Discovery:** `llm-usage doctor --json` every 1800 s. Import only sources whose status is found and that aren't in the excluded list.
- **Import:** `llm-usage events --source <comma list> --since <ISO cursor>` every 300 s, parsing one JSON object per line with these fields: `source, sessionId, timestamp, provider, model, inputTokens, outputTokens, reasoningTokens, cacheReadTokens, cacheWriteTokens, costUsd, costMode, repoRoot`.
  - Each line becomes a session `<source>:<sessionId>` (`imported=True`, harness `<source>`, project `repoRoot`) and one `assistant` event with `ext_id = sha1(line)[:16]`, out = output + reasoning.
  - The cursor is the max timestamp seen.
- **Status:** `session_status` treats `imported` sessions by recency only, using the Global Constraints thresholds. Extend the function with `imported: bool = False`.

**Steps:**
- [ ] Write the failing tests:
  - `test_doctor_found_sources_imported`;
  - `test_native_sources_excluded`;
  - `test_reimport_is_idempotent`;
  - `test_imported_status_by_recency`;
  - `test_missing_tool_marks_partial`;
  - `test_env_scrubbed`: the fake records the environment; no `*KEY*` reaches it;
  - `test_install_requires_token`.
- [ ] FAIL, implement, PASS.
- [ ] Commit `feat(m4): long-tail importer via llm-usage-metrics 0.12.1`.

### Task 9: Web UI for the new sources

**Files:**
- Modify:
  - `web/src/office/colors.ts`;
  - the shared retained renderer under `web/src/office/` (dotted ring for `imported`);
  - `web/src/api/types.ts` (`SessionRow.imported`, `LimitCard.balance`, `Settings.zylos_previews`, `deepseek_alert`, `price_sources`, `Health[*].tested`);
  - `web/src/pages/Limits.tsx`, `Settings.tsx`;
  - `web/src/components/Inspector.tsx`.
- Test: the existing page tests, extended.

**Behaviour:**
- **Office:**
  - New harness colours and labels.
  - Imported NPCs draw their status ring dotted.
  - The inspector shows "Imported from llm-usage-metrics; no live timeline." for imported sessions instead of an empty timeline.
- **Limits:** the DeepSeek card shows balance (currency, total), granted and topped-up, the "Measured" badge, the age, and a low-balance warning.
- **Settings:**
  - Sources list "untested on this PC" for `tested: 'upstream'`.
  - "Install long-tail importer" button: POST, then show progress from `/api/health.importer`.
  - "Show Zylos message previews" toggle and "DeepSeek low-balance alert" number.
  - The model prices list shows a source badge (yours / LiteLLM / unpriced).

**Steps:**
- [ ] Write the failing tests:
  - `harnessLabel maps new harnesses`;
  - `Inspector shows imported note`;
  - `Limits shows DeepSeek balance card`;
  - `Settings shows untested label and price source`;
  - `Settings install importer posts`.
- [ ] FAIL, implement, PASS. Build passes.
- [ ] Commit `feat(m4): UI for new sources`.

### Task 10: Real-data verification and docs

**Steps:**
- [ ] **Antigravity hand count:** write an independent decoder script in the scratchpad (its own varint walker, not `hq` code). Compare its token totals with Agent HQ for the largest and the oldest conversation.
  - **If they match:** set `AntigravityAdapter.tested = 'real'`.
  - **If they don't:** stop and fix before continuing.
- [ ] **Regression checks:**
  - Kimi: the real session gives 0 warnings.
  - Codex: totals equal the 0.3.1 numbers (696 sessions, 12.07 B tokens) at the same moment's data.
  - Full pass with all readers `ok` (importer `disabled` unless installed).
- [ ] **Importer on the real machine:**
  - Install it from Settings and run doctor. Record which sources are found; none of the long tail is installed here, so expect "found: none" and a healthy importer.
  - With no Node on PATH, check that the importer shows `partial` with a reason.
- [ ] **Docs:**
  - README: the new sources table, the DeepSeek key, the importer, and privacy (the first network call, Zylos previews).
  - CHANGELOG `0.4.0`, with the verification numbers.
  - The Brain vault note.
- [ ] **Independent review** (fresh reviewer that built none of this), fixes, then merge.
