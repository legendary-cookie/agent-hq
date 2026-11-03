# Agent HQ run mode (Milestone 5) — design

Status: draft for review, 2026-11-03. Builds on the M1–M4 design ([main spec](2026-10-30-agent-hq-design.md), [sources](2026-11-02-agent-hq-sources-design.md)).

## 1. Purpose

Agent HQ today only watches agents. Run mode makes it a "double system", in the spirit of Pantheon: it can also start, steer and stop agents, and coordinate several of them. The user chose to have all three uses, each one picked per run:

| Stage | Mode | What the user does |
|---|---|---|
| M5a | **Dispatch** | Pick a tool, model, project and task; press Run; watch it live; stop it; review its diff and merge or discard. |
| M5b | **Steer** | Everything in Dispatch, plus follow-up messages to a running agent and an approvals inbox for tool calls. |
| M5c | **Orchestrate** | Give a goal; Agent HQ splits it into runs and routes each to an agent and account through provider-usage. |

Each stage gets its own spec and plan. **This spec fully specifies M5a and the shared run engine.** M5b and M5c are outlined in section 10 so the engine leaves room for them; they are specified later.

### Decisions made with the user

| Topic | Decision |
|---|---|
| Scope | All three modes, user picks per run; built in the order M5a → M5b → M5c. |
| Autonomy | Per-run level: Read-only, Edit project folder (default), Full auto (extra confirm, only where enforceable). |
| Workspace | A git worktree and branch per editing run; diff, then Merge or Discard. Non-git folders: In place, with a warning. |
| Projects | Allowlist in `config/projects.json`, never written by Agent HQ. |
| Quota guard | Per-run choice of Warn, Block-and-suggest (default) or Auto-route. |
| Account pooling | Several account profiles per provider (for example three ChatGPT subscriptions), each with its own config folder; the pool picks the profile with the most measured headroom. |
| Engine | Runner inside the Agent HQ server (approach A). |

### Success criteria for M5a

- A run started from the UI appears live within 2 s, links to the session the existing reader finds, and its tokens and value match what the reader reports with no double counting.
- Stop ends the whole process tree within 5 s on Windows and in WSL.
- Every installed tool on this PC either passes one tiny real read-only run or is hidden with an "untested" label.
- No security test in section 8 fails, and an independent review from a different provider finds no open high or medium issue.

## 2. Tools and how each is launched

Checked on this PC on 2026-11-03. Each launcher builds a fixed argument list; nothing goes through a shell.

| Tool | Version | Headless command | Live output | Session id from |
|---|---|---|---|---|
| Claude Code | 2.1.296 | `claude -p --output-format stream-json --verbose` | stream-json | `system/init` event |
| Codex | 0.164.0 | `wsl -d Debian --cd <wsl path> -- codex exec --json -m <model> -s <sandbox> -` | JSON lines | `thread.started` / session event |
| Antigravity | 1.4.9 | `agy -p --output-format stream-json` | stream-json | init event |
| opencode | 1.18.34 | `opencode run --format json -m <provider/model>` | JSON events | event `sessionID` |
| Kimi | 0.29.1 | `kimi -p <prompt> --output-format stream-json` | stream-json | session event |
| Gemini CLI | 0.52.0 | `gemini -p <prompt>` | text | none; linked by time and project (see 5.4) |
| DeepSeek Harness | 0.2.0-rc.2 | `dsh headless <prompt>` | text | none; linked by time and project |
| Hermes | 2026.9.24 | `hermes -z <prompt> --in <dir> -m <model> --usage-file <tmp>` | text | usage file |

The exact event names are confirmed from recorded samples during implementation (section 8); a launcher whose samples don't match stays hidden.

**Level → tool flags.** A tool offers only the levels it can enforce.

| Tool | Read-only | Edit project folder | Full auto |
|---|---|---|---|
| Claude Code | `--permission-mode plan` | `--permission-mode acceptEdits` | `--dangerously-skip-permissions`, only inside a worktree |
| Codex | `-s read-only` | `-s workspace-write` | `-s workspace-write -c sandbox_workspace_write.network_access=true` |
| Antigravity | not offered unless a read-only flag is confirmed | default | `--dangerously-skip-permissions`, only inside a worktree |
| opencode | not offered unless confirmed | default agent | not offered |
| Kimi | not offered unless confirmed | default | `--yolo`, only inside a worktree |
| Gemini CLI | default without `--yolo` | not offered unless confirmed | `--sandbox --yolo` |
| DeepSeek Harness | not offered unless confirmed | default | not offered |
| Hermes | not offered | default | `--yolo`, only inside a worktree |

"Not offered unless confirmed" means the launcher ships with that level disabled; enabling it needs a recorded sample proving the tool refuses writes in that mode.

## 3. Architecture

New package `hq/run/`. Each file has one job:

| File | Responsibility |
|---|---|
| `launchers/<tool>.py` | `build(req, profile, workdir) -> Launch(argv, env, cwd, stdin_text)`; `levels() -> set[str]`; `parse(line) -> StreamEvent | None`. |
| `stream.py` | `StreamEvent(kind, session_id=None, text=None, rate_limited=False)`; kinds: `started`, `session`, `working`, `waiting`, `done`, `error`. No token counting. |
| `workspace.py` | `create(project, run_id) -> Path`, `diff(run) -> str`, `merge(run) -> MergeResult`, `discard(run)`. |
| `pool.py` | Account profiles; `pick(tool, model, store) -> Profile` from measured limits. |
| `guard.py` | `check(req, store, pu) -> Verdict(allowed, warnings, suggestion)`; modes `warn`, `block` (default), `auto`. |
| `nonce.py` | Single-use confirm nonces bound to a request hash. |
| `runner.py` | Owns child processes: start, one reader thread per run, stop with tree kill, time limit, restart recovery, provider-usage logging. |
| `config.py` | Loads `config/run.json` and `config/projects.json`; both read-only for Agent HQ. |

`hq/server.py` gains the `/api/runs*` routes (section 6) and calls `runner`; `hq/store.py` gains the `runs` table. The collector and readers are unchanged.

### 3.1 Run flow

1. The UI calls `GET /api/runs/options`, then `POST /api/runs/prepare` with the form.
2. `prepare` validates the request (allowlist, level allowed, model pattern), runs the guard and the pool pick, and returns the verdict and a nonce.
3. The UI shows the verdict; on Start it calls `POST /api/runs` with the same request and the nonce.
4. The runner creates the worktree (editing levels on git projects), builds the launch, and starts the child in a Job Object (Windows) or a new process group (Linux).
5. The reader thread parses each output line, updates the run's status, keeps the line in a 500-line in-memory ring, and pushes a `run` event on the live feed.
6. When a session id appears, the run stores it. The existing reader picks up the session's log; the run's tokens and value are read from that session.
7. On exit the run becomes `done`, `failed`, `stopped`, `timed_out`, `rate_limited` or `interrupted`; the provider-usage record is written; the diff becomes available.

### 3.2 Data

New table:

```sql
CREATE TABLE runs (
  id TEXT PRIMARY KEY,            -- 'run_' + 12 random url-safe chars
  tool TEXT NOT NULL, model TEXT, account TEXT,
  project TEXT NOT NULL, workdir TEXT NOT NULL, branch TEXT,
  level TEXT NOT NULL,            -- read_only | edit | full_auto
  guard_mode TEXT NOT NULL,       -- warn | block | auto
  mode TEXT NOT NULL DEFAULT 'dispatch',
  status TEXT NOT NULL,           -- queued|starting|running|done|failed|stopped|timed_out|rate_limited|interrupted
  session_id TEXT,                -- links to sessions.id once known
  prompt_preview TEXT,            -- first 200 characters only
  started_at REAL, ended_at REAL, exit_code INTEGER,
  time_limit_s INTEGER NOT NULL,
  merge_state TEXT                -- null|merged|discarded|conflict
);
```

Full prompts and raw output are never stored in the database.

## 4. Configuration

All three files are read-only for Agent HQ; the UI links to them.

**`config/run.json`**

```json
{
  "run_mode": false,
  "max_concurrent": 3,
  "default_time_limit_min": 30,
  "max_time_limit_min": 240,
  "pass_env": {"opencode": ["OPENROUTER_API_KEY"]}
}
```

`pass_env` names, by variable name only, an API key one tool needs; its value is read from Agent HQ's own environment at launch and never logged or returned.

**`config/projects.json`**

```json
{"projects": [{"name": "agent-hq", "path": "C:\\Users\\Jane Doe\\ao\\projects\\agent-hq"}]}
```

Settings suggests recent project folders from the logs for the user to copy in.

**Account profiles**, in the existing `config/accounts.json`, gain an optional `profiles` list per account:

```json
{"id": "chatgpt-2", "tool": "codex", "config_dir": "/root/.codex-chatgpt-2"}
```

The launcher sets `CODEX_HOME`, `CLAUDE_CONFIG_DIR` or the tool's equivalent to `config_dir`. The user logs in once per profile in a terminal. Agent HQ never reads or copies files inside `config_dir`; the readers gain the profile folders as extra session paths so per-account limits are measured.

Users should check that each provider's terms allow using several accounts this way; the Run form always shows which profile was picked.

## 5. Behaviour

### 5.1 Guard modes

- **Warn:** show quota state and warnings; Start always allowed.
- **Block-and-suggest (default):** a model provider-usage marks blocked can't be started; one above 80% of any window gets a warning; recommend's pick is shown beside the user's choice.
- **Auto-route:** the user picks task kind and minimum tier; the guard fills tool, model and profile from `recommend`, skipping blocked ones.

### 5.2 Pool pick

For "Pool: best", choose the profile of the chosen tool with the lowest highest-window usage; skip profiles blocked until a reset in the future; ties go to the least recently used. A specific profile can be picked instead. If every profile is blocked, `prepare` returns `allowed: false` with the earliest reset.

### 5.3 Workspace

- Editing levels on a git project: `git worktree add -b hq/run-<id> <data>/worktrees/<id> HEAD`.
- Read-only runs, and non-git folders: the project folder itself (non-git shows a warning).
- Codex in WSL: the worktree path is converted to `/mnt/c/...`.
- **Merge** (separate click, after the diff was shown): fast-forward if possible, otherwise a merge commit on the project's current branch; never a push. On conflict: `git merge --abort`, `merge_state = 'conflict'`, the branch is kept.
- **Discard:** `git worktree remove --force` and `git branch -D hq/run-<id>`.

### 5.4 Linking a run to its session

Primary: the session id from the stream. Fallback for tools without one (Gemini CLI, DeepSeek Harness): the newest session of that harness whose project matches the run's workdir and whose first event falls between the run's start and start + 60 s. If neither matches, the run shows "not linked to a session". A linked session gets a "launched by HQ" badge in the office and Agents pages.

### 5.5 Lifecycle and limits

- At most `max_concurrent` runs in `starting` or `running`; more get `409`.
- The time limit kills the tree and sets `timed_out`.
- Stop kills the tree (Job Object terminate on Windows; `killpg` on Linux, also inside WSL via `wsl -d Debian -- kill`), status `stopped`.
- On Agent HQ exit all children are killed. On startup any run still `starting` or `running` is set to `interrupted`.
- A 429 or rate-limit message in the stream sets `rate_limited`, calls provider-usage `report_limit`, and marks the profile blocked until its reset.
- Every run is logged to provider-usage (`log_call`) with tool, model, task kind `hq-run`, duration and outcome.

## 6. API

All routes need the token (`X-HQ-Token`) and, for POST, a same-origin `Origin`. When `run_mode` is false every route except `options` returns `403 {"error": "run mode is off"}`.

| Route | Purpose |
|---|---|
| `GET /api/runs/options` | `run_mode`, installed tools with their levels and models, projects, profiles with measured limits, defaults. |
| `POST /api/runs/prepare` | Body: `{tool, model, project, prompt, level, guard_mode, profile, time_limit_min}`. Returns `{verdict, nonce, expires_at}`. |
| `POST /api/runs` | Same body plus `nonce`; `201 {run}`. |
| `GET /api/runs`, `GET /api/runs/{id}` | Run records. |
| `GET /api/runs/{id}/output` | The in-memory ring (last 500 lines), or `410` after a restart. |
| `POST /api/runs/{id}/stop` | Stop. |
| `GET /api/runs/{id}/diff` | Diff of the run branch against its base, capped at 2 MB. |
| `POST /api/runs/{id}/merge`, `/discard` | Act on the worktree. |

The live feed gains `{"type": "run", "id", "status"}` events.

### 6.1 UI design rules

The new screens (Run sheet, Runs page, run inspector, diff view) follow the Apple-style rules from emilkowalski/skills `apple-design`, which the user pointed to on 2026-11-03:

- **Run sheet:** a translucent sheet (`backdrop-filter` blur, semi-transparent fill) over a dimming scrim, since starting a run is a modal task. It springs open from the Run button (critically damped: damping 1.0, response about 0.35 s) and closes along the same path.
- **Feedback:** the pressed state shows on pointer-down. A pressed Start shows "Starting…" at once, and the run row appears before the process reports in.
- **Motion:** animate transform and opacity only, and keep every animation interruptible. With `prefers-reduced-motion`, use cross-fades instead of springs. With `prefers-reduced-transparency`, use solid surfaces.
- **Confirmation only where it's destructive or costly:** Full auto, Discard, and Merge (after the diff). Stop is instant, with no undo needed.
- **Type and colour:** system font with optical sizing; large headings tightened (about -0.02em); higher-contrast text on translucent surfaces; edge fades instead of divider lines; light and dark modes.
- **Labels:** specific verbs ("Start run", "Stop run", "Merge into main", "Discard branch"), never "OK".

## 7. Security

1. **Off by default.** Only `config/run.json` turns run mode on; `PUT /api/settings` can't.
2. **Confirm nonce.** `prepare` returns a 32-byte random nonce, single use, valid 60 s, bound to SHA-256 of the canonical request JSON (tool, model, project, prompt, level, guard mode, profile, time limit). `POST /api/runs` with a missing, used, expired or mismatched nonce gets `403`.
3. **Executables.** Resolved once at startup with `shutil.which`; a result inside the current folder, any allowlisted project, or `<data>/worktrees` is refused.
4. **Arguments.** Fixed lists per tool; the prompt is one argument or stdin; model names must match `^[A-Za-z0-9._:/-]{1,80}$`; prompts are capped at 100,000 characters.
5. **Paths.** The project's real path (symlinks and junctions resolved) must equal an allowlisted real path; the existing forbidden-path checks apply to projects, worktrees and profile folders.
6. **Environment.** Children get a minimal environment: `PATH`, `HOME`/`USERPROFILE`, `SYSTEMROOT`, `TEMP`/`TMP`, `LANG`, plus the non-secret startup names Windows and POSIX tools need (`APPDATA`, `LOCALAPPDATA`, `PATHEXT`, `COMSPEC`, `SYSTEMDRIVE`, `WINDIR`, `PROGRAMDATA`, `PROGRAMFILES`, `USERNAME`, `HOMEDRIVE`, `HOMEPATH`, `USER`, `LOGNAME`, `SHELL`, `TERM`, `LC_ALL`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_CACHE_HOME`; full list in `hq/run/env.py`), the profile's config-folder variable, and `pass_env` entries for that tool. Anything else matching `KEY|TOKEN|SECRET|PASSWORD` is never passed.
7. **Full auto** needs a second confirm in the UI, is offered only per the table in section 2, and every use is logged.
8. **Merge** is never automatic and never pushes.
9. **Output** stays in memory; the database keeps status and the 200-character preview only.
10. **Resource limits:** concurrency, time limit and tree kill as in 5.5.

## 8. Testing

- **Launchers:** golden tests of `argv` and `env` per tool and level; dropped variables absent; disallowed levels refused.
- **Stream parsers:** recorded samples per tool from short harmless real runs, with private text replaced by invented values.
- **Runner:** a fake agent script (prints JSON, sleeps, spawns a grandchild, exits non-zero) covering start, stop, tree kill, time limit, concurrency `409` and restart recovery.
- **Workspace:** temporary git repos: create, diff, fast-forward merge, merge commit, conflict abort, discard, non-git warning.
- **Pool and guard:** blocked profile skipped, least-used chosen, all blocked → not allowed, each guard mode's verdict.
- **Security:** missing, replayed, expired and mismatched nonce; wrong Origin; run mode off; non-allowlisted, `..` and symlinked project; executable in the current folder; bad model name; oversize prompt.
- **Real-tool check:** one tiny read-only run per installed tool on this PC, checking the session link and that token totals equal the reader's. Failing tools stay hidden with an "untested" label.
- **Review:** independent security review by a different provider before merging.

## 9. Error handling

| Failure | Result |
|---|---|
| Tool missing or crashes on start | `failed`, exit code and last 20 lines kept; worktree kept |
| Unparseable output line | kept in output, counted as a warning; run continues |
| No session id | run works; "not linked to a session" |
| Time limit | `timed_out` |
| Agent HQ restart mid-run | `interrupted` |
| Merge conflict | merge aborted, branch kept, `merge_state = 'conflict'` |
| Rate limit in stream | `rate_limited`; `report_limit`; profile blocked until reset |
| Concurrency or all profiles blocked | `409` with the reason |

## 10. Later stages (outline only)

**M5b Steer.** Keep stdin open for tools with a stream-json input (Claude Code, Antigravity, Kimi) to send follow-up messages; resume-based follow-ups for the others (`codex exec resume`, `opencode run -s`, `hermes --resume`). Approvals inbox through each tool's permission-prompt hook where one exists (for example Claude Code's permission-prompt tool); tools without one can't use an "Ask me" level. The engine already keeps the reader thread and a stdin handle per run.

**M5c Orchestrate.** A goal becomes a plan of runs (one planner run, then routed worker runs) with dependencies; routing uses the Auto-route guard and the pool; the office shows the team gathered at a table. Needs its own design for plan review, budget caps and failure handling.

## 11. Out of scope for M5a

Follow-up messages, approvals, multi-run goals, remote or SSH hosts, pushing branches, scheduling runs, and storing full transcripts.
