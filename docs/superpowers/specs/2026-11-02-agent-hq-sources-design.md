# Agent HQ M4: more sources (design spec)

Date: 2026-11-02. Status: draft for review. Builds on `2026-10-30-agent-hq-design.md` (the reader model, store, KPIs, limits and UI described there are unchanged unless stated).

## 1. Purpose

Watch more of the agents the user runs or plans to run, value their work, and show the live balance of a paid API. Agreed with the user:
- **Approach C, hybrid:** our own full-detail readers for the tools the user named or uses, plus an import of `llm-usage-metrics` for the long tail.
- **DeepSeek:** both the DeepSeek Harness CLI and DeepSeek as a model provider with its API balance.
- **Zylos:** not installed yet. Build its reader from the published schema, so it works once installed.

Success means:
- Each new source shows on the Dashboard, the Agents page, Limits (where it has limits) and the Office.
- Value is computed for most models instead of "unpriced".
- Readers checked against real logs say so; the others are labelled "untested on this PC".

Non-goals: remote machines over SSH, run mode (M5), and office extras (M6).

## 2. Sources

### 2.1 Our own readers (full detail: status, tool calls, tokens, subagents)

| Reader | Location | Format | Notes |
|---|---|---|---|
| `kimi` | `~/.kimi-code/sessions/<workdir>/session_<id>/agents/<agent>/wire.jsonl` and `state.json`; also `~/.kimi/sessions` for Kimi CLI | wire JSONL (`type` per record: `metadata`, `config.update`, `tools.set_active_tools`, messages, tool calls, usage) | `agents/<name>` other than `main` are subagents of the session. The only real session on this PC is empty (4 setup records), so token parsing is checked against upstream test logs. |
| `dsh` (DeepSeek Harness) | `$DSH_HOME/sessions` or `~/.dsh/sessions` | `session*.jsonl` and `session.v3.jsonl.zstd` | The `.zstd` file is a series of independent zstd frames, one per flush, and its last frame may be torn mid-write. It is read frame by frame via `compression.zstd`, with the cursor = size + mtime. Not installed here. |
| `zylos` | `~/zylos/comm-bridge/c4.db` (SQLite, read-only), `~/zylos/activity.log`; Claude Code and Codex sessions whose project is under `~/zylos` | `conversations(id, direction, channel, endpoint_id, content, status, …, created_at)` | **Relabelling:** the Claude/Codex sessions under `~/zylos` get `harness = 'zylos'`, and keep their real provider and model. **Message events:** `user` for `in`, `assistant` for `out`, with tool set to the channel (telegram/lark/discord/scheduler/system). **Message text:** previews are **off by default**, because `content` is private chat. Settings has a toggle, "Show Zylos message previews". Not installed here. |
| Antigravity tokens | existing `~/.gemini/antigravity-cli/conversations/*.db` | protobuf blobs in `gen_metadata.data` (chat-model message, usage field 4) | Port llm-usage-metrics' `antigravity-proto-reader` (MIT) to stdlib Python: a minimal protobuf varint and length-delimited walker, no dependency. This replaces "unknown" tokens and fills in the model. |

### 2.2 DeepSeek as a provider

- **Provider recognition:** `deepseek-*` and `*/deepseek*` models from any harness (opencode, NineRouter, dsh) get `provider = 'deepseek'`, so the Dashboard groups by provider correctly.
- **Balance:**
  - Fetched from `GET https://api.deepseek.com/user/balance` with `Authorization: Bearer <key>`, every 15 min.
  - The key comes from env `DEEPSEEK_API_KEY`, else the file `<db folder>/secrets/deepseek_api_key`. The file is per-user, never in `config/`, git, logs or notes.
  - No key means no request.
- **Storage:** the result goes into a new `balances` table (`account_id, currency, total, granted, topped_up, recorded_at`).
- **Display:** a "DeepSeek API" card on the Limits page.
- **Alert:** below a threshold (default 5.00, in the balance currency).
- **First network call:** this is the first outbound request Agent HQ makes. It goes only to `api.deepseek.com`, with a 10 s timeout. Errors show in health without exposing the key. The README privacy section lists it.

### 2.3 Long-tail import (totals and events only)

- **Tool:** `llm-usage-metrics` **v0.12.1**, installed with `npm install --prefix <db folder>/tools llm-usage-metrics@0.12.1`, a private folder that isn't global. The install runs from **Settings → Sources → "Install long-tail importer"**, never automatically.
- **Discovery:** `llm-usage doctor --json`, every 30 min. Only sources marked found are imported: Copilot CLI, Goose, Amp, Qwen CLI, Cline, RooCode, KiloCode, Droid, pi and OpenClaw. Sources Agent HQ reads itself (claude, codex, opencode, gemini, antigravity, kimi, dsh) are **excluded** to avoid double counting.
- **Import:** `llm-usage events --source <found> --since <cursor>`, every 5 min. Each line follows `schema/events-line.v1.schema.json` (`source, sessionId, timestamp, provider, model, …tokens, costUsd, repoRoot, costMode`).
  - It becomes session `<source>:<sessionId>` with `harness = <source>`, plus one `assistant` event per line carrying the tokens. The event id is a hash of the line, so re-imports dedupe.
  - `costUsd` is stored only when `costMode` is `explicit` (logged by the tool); otherwise our price table values it.
- **Status:** "imported" sessions get status from recency only: active under 10 min, idle under 24 h, done after. They are never "working" or "needs you".
- **Office:** a dotted ring marks "imported, totals only". The inspector shows "Imported from llm-usage-metrics; no live timeline."
- **Failure handling:** no Node, no tool, or a failing command marks the importer's health `partial` with the reason, and nothing else is affected.

### 2.4 Prices

- **Bundled snapshot:** `config/prices.litellm.json`, LiteLLM pricing (MIT) taken from llm-usage-metrics v0.12.1's `litellm-pricing-snapshot.json`, plus its `litellm-model-map.json`, converted to our `Price` shape (USD per 1M tokens).
- **Lookup order:** `config/prices.json` (the user's) → exact LiteLLM name → model-map alias → unpriced.
- **Settings:** each model shows its price source ("yours", "LiteLLM", "unpriced"), and any price can be overridden.
- **Updates:** the snapshot is never fetched at runtime. It is refreshed by a script, `tools/update_prices.py`, run on purpose and committed.

## 3. Cross-cutting

- **Health** gains a `tested: "real" | "upstream"` field per reader, shown in Settings as "untested on this PC" for `upstream`. It's set from a constant per reader and changes only when a real-log check passes.
- **Office:** zone colours for the new harnesses: kimi `#6f6af8`, dsh `#4d6bfe`, zylos `#e0598b`; imported long-tail sources use `#8e8e93` plus the dotted ring. `harnessLabel` gains Kimi, DeepSeek Harness, Zylos, Copilot CLI, Goose, Amp, Qwen, Cline, Roo Code, Kilo Code, Droid, pi, OpenClaw.
- **Licences:** a `NOTICE` file credits llm-usage-metrics (MIT, © its authors) for the ported Antigravity decoder, test logs and model map, and LiteLLM (MIT) for prices.
- **Security:**
  - All readers stay read-only, and the forbidden-path guard applies to every new path.
  - The importer runs a fixed executable from the private tools folder, with a fixed argument list (no shell), a 60 s timeout and its own environment minus anything matching `*KEY*`/`*TOKEN*`/`*SECRET*`.
  - The DeepSeek key is never logged or returned by any API.

## 4. Verification

- **Real logs on this PC:**
  - Antigravity tokens: hand count from the raw step blobs, with an independent decoder script, against Agent HQ for the largest and the oldest conversation.
  - Kimi: the empty session parses with 0 warnings.
  - Codex: no regression from the 0.3.1 checks.
- **Upstream test logs:** for dsh, Kimi tokens, Zylos (from zylos-core's `migration-history` fixtures and `init-db.sql`) and the importer mapping. These fixtures are copied into `tests/fixtures/`, with credit.
- **DeepSeek balance:** a test against a fake HTTP server only. No real key is used unless the user adds one.
- **Independent review** before merge, as in M1–M3.
