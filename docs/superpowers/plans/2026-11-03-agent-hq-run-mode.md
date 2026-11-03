# Agent HQ Run Mode (M5a Dispatch) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Start, watch, stop and review one-shot agent runs from Agent HQ (Dispatch), on a run engine that Steer and Orchestrate can build on later.

**Architecture:**
- New package `hq/run/`.
- The runner lives inside the existing server process. It starts each tool as a child process with a fixed argument list, parses the tool's JSON output for status only, and links the run to the session the existing readers already find, which is where tokens and value come from.
- Editing runs work in a git worktree per run.
- Every start goes through allowlist, guard, pool and a single-use nonce.

**Tech Stack:**
- Backend: Python 3.11+ stdlib only (`subprocess`, `threading`, `ctypes` for the Windows Job Object), SQLite.
- Frontend: React 18.3.1 + TypeScript + Vite 5.4.11 (existing `web/`), vitest.

**Spec:** [docs/superpowers/specs/2026-11-03-agent-hq-run-mode-design.md](../specs/2026-11-03-agent-hq-run-mode-design.md). Read it before every task; section numbers below refer to it.

## Global Constraints

- **Dependencies:** Python stdlib only; no new npm dependencies. Never run `npm install` (use the existing `web/node_modules`).
- **Off by default:**
  - Run mode is off unless `config/run.json` has `"run_mode": true`.
  - Agent HQ never writes `config/run.json`, `config/projects.json` or `accounts.json` profile entries.
  - `PUT /api/settings` cannot change them.
- **No shell anywhere:**
  - `subprocess` with list `argv` and `shell=False`.
  - The prompt goes on stdin, or as exactly one argv element.
- **Input limits:**
  - Model names must match `^[A-Za-z0-9._:/-]{1,80}$`.
  - Prompts are at most 100,000 characters.
  - Time limit is 1–240 min (default 30).
  - `max_concurrent` defaults to 3.
- **Child environment:** only `PATH`, `HOME`, `USERPROFILE`, `SYSTEMROOT`, `TEMP`, `TMP`, `LANG`, plus the non-secret startup names Windows and POSIX tools need (`APPDATA`, `LOCALAPPDATA`, `PATHEXT`, `COMSPEC`, `SYSTEMDRIVE`, `WINDIR`, `PROGRAMDATA`, `PROGRAMFILES`, `USERNAME`, `HOMEDRIVE`, `HOMEPATH`, `USER`, `LOGNAME`, `SHELL`, `TERM`, `LC_ALL`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_CACHE_HOME`; full list in `hq/run/env.py`), the profile's config-folder variable, and that tool's `pass_env` names. Never pass anything else matching `KEY|TOKEN|SECRET|PASSWORD` (case-insensitive).
- **Never read, copy or log** files inside a profile `config_dir`, or any path `hq.config.is_forbidden` rejects.
- **Storage:**
  - The DB stores at most 200 characters of the prompt (`prompt_preview`).
  - Raw output lives only in a 500-line in-memory ring per run.
- **Git:** merge is a separate request, never automatic, and never pushes. Run branches are `hq/run-<id>`; worktrees live under `<data>/worktrees/<id>`.
- **Codex** always runs as `wsl -d Debian --cd <wsl path> -- ...`, with the prompt on stdin.
- **Tests must not:**
  - call any real agent tool (except Task 11, which is manual);
  - use the network;
  - touch `~/.claude`, `~/.codex`, `~/.gemini`, `~/.hermes`, `~/.kimi*`, `~/.dsh` or `~/zylos`.
- **Style:**
  - Match existing code: `unittest`, tests in `tests/test_run_*.py`.
  - Windows-safe tests: use `contextlib.closing` for sqlite connections, and temp dirs from `tempfile.TemporaryDirectory`.
- **Run IDs:** `'run_' + secrets.token_urlsafe(9)` (12 url-safe chars).

## Review Focus

1. **Prompt text that looks like flags or shell** (`--dangerously-skip-permissions`, `; rm -rf`, `$(...)`, newlines) must stay one inert argument or stdin. A prompt starting with `-` must not be parsed as a flag. Pinned in Task 4: `test_prompt_is_inert`.
2. **Project paths with spaces and junctions** (`C:\Users\Jane Doe\...`, a junction into an allowlisted folder) must resolve correctly. The WSL path conversion must keep the space. Pinned in Task 2 (`test_junction_outside_allowlist_refused`) and Task 4 (`test_wsl_path_with_space`).
3. **Stop while starting:** a stop that arrives before the child has spawned, or between spawn and Job Object assignment, must still end with no orphan process. Pinned in Task 7: `test_stop_during_start`.
4. **Two runs on one project at once** must get separate worktrees and branches, with no shared git lock failure surfacing as a crash. Pinned in Task 5: `test_two_worktrees_same_repo`.
5. **Agent HQ shutdown with runs active** must kill every child, including a grandchild that detached its own console. Pinned in Task 7: `test_shutdown_kills_grandchild`.

---

### Task 1: Run config, projects allowlist and `runs` table

**Files:**
- Create: `hq/run/__init__.py`, `hq/run/config.py`
- Modify: `hq/store.py` (schema + `runs` helpers), `hq/config.py` (`Account.profiles`)
- Test: `tests/test_run_config.py`

**Interfaces:**
- Produces:
  - `RunConfig(run_mode: bool, max_concurrent: int, default_time_limit_min: int, max_time_limit_min: int, pass_env: dict[str, list[str]], show_untested: bool = False)`
  - `Project(name: str, path: Path)` (`path` is the resolved real path)
  - `load_run_config(config_dir: Path) -> RunConfig`: a missing file gives defaults with `run_mode=False`.
  - `load_projects(config_dir: Path) -> list[Project]`: missing file → `[]`; forbidden or non-directory paths are dropped, with a warning list.
  - `Profile(id: str, tool: str, config_dir: str, account_id: str)`
  - `Config.accounts[i].profiles: list[Profile]` (default `[]`)
  - `Store.insert_run(rec: dict)`, `Store.update_run(id: str, **fields)`, `Store.get_run(id) -> dict | None`, `Store.list_runs(limit=100) -> list[dict]`, `Store.mark_interrupted() -> int`

- [ ] **Step 1: Write failing tests**
  - `test_missing_run_json_is_off` → `run_mode is False`, `max_concurrent == 3`.
  - `test_run_json_values_clamped`: `max_time_limit_min` 999 → 240; `max_concurrent` 0 → 1.
  - `test_projects_resolve_and_drop_forbidden`: a dir named `secrets` is dropped; a normal dir is kept with a resolved path.
  - `test_profiles_parsed_and_saved_unchanged`: `save_config` round-trips `profiles`.
  - `test_runs_table_roundtrip`: insert, update status, get, list newest first.
  - `test_mark_interrupted`: rows in `starting`/`running` become `interrupted`; return value 2.
  - `test_runs_migration_on_existing_db`: an old DB without `runs` gains it.
- [ ] **Step 2:** Run `python -m unittest tests.test_run_config -v`. Expect them to FAIL (module missing).
- [ ] **Step 3: Implement**
  - Add the `runs` table exactly as in spec §3.2, with `CREATE TABLE IF NOT EXISTS` in `_SCHEMA`.
  - `update_run` only accepts known column names.
  - `load_run_config` clamps values; `pass_env` values must be lists of names matching `^[A-Z_][A-Z0-9_]*$`.
- [ ] **Step 4:** Run the same command. Expect PASS. Then run `python -m unittest discover -s tests` and expect it to pass (202+).
- [ ] **Step 5: Commit** `feat(run): run config, project allowlist, runs table`

### Task 2: Request validation and confirm nonce

**Files:**
- Create: `hq/run/request.py`, `hq/run/nonce.py`
- Test: `tests/test_run_request.py`

**Interfaces:**
- Consumes: `RunConfig`, `Project` (Task 1).
- Produces:
  - `RunRequest(tool, model, project, prompt, level, guard_mode, profile, time_limit_min)`, a frozen dataclass.
  - `parse_request(data: dict, run_cfg: RunConfig, projects: list[Project], tools: dict[str, set[str]]) -> RunRequest`. It raises `ValueError` with a user-readable message. `tools` maps a tool name to its allowed levels.
  - `request_hash(req) -> str`: SHA-256 hex of canonical JSON (sorted keys, `separators=(',',':')`).
  - `NonceBook(ttl_s=60, clock=time.monotonic)`, with `.issue(req) -> (nonce, expires_at)` and `.redeem(nonce, req) -> bool`. Redeem is single use, constant-time compare, and expired entries are pruned on each call.

- [ ] **Step 1: Write failing tests**
  - `test_unknown_tool_refused`
  - `test_level_not_offered_refused`
  - `test_bad_model_refused` (`'gpt 6'`, `'a;b'`, 81 chars)
  - `test_prompt_too_long_refused` (100,001)
  - `test_empty_prompt_refused`
  - `test_time_limit_bounds` (0 and 241 refused; `None` gives the default)
  - `test_project_must_be_allowlisted_by_name`
  - `test_junction_outside_allowlist_refused`: a symlink or junction inside a temp dir pointing outside → refused. Skip only if a link can't be created on this OS.
  - `test_guard_mode_values` (`warn|block|auto` only)
  - `test_nonce_single_use`
  - `test_nonce_expires` (fake clock +61)
  - `test_nonce_bound_to_request` (changing the prompt → False)
  - `test_hash_stable_key_order`
- [ ] **Step 2:** Run. Expect FAIL.
- [ ] **Step 3: Implement**
  - The project is chosen by allowlist `name`. The resolved real path of the stored `Project.path` must still equal `Path(path).resolve()` at parse time; otherwise refuse.
  - The nonce is `secrets.token_urlsafe(32)`.
- [ ] **Step 4:** Run. Expect PASS.
- [ ] **Step 5: Commit** `feat(run): request validation and confirm nonce`

### Task 3: Clean environment and executable resolution

**Files:**
- Create: `hq/run/env.py`
- Test: `tests/test_run_env.py`

**Interfaces:**
- Produces:
  - `child_env(base: Mapping[str,str], extra: dict[str,str], pass_names: list[str]) -> dict[str,str]`
  - `resolve_exe(name: str, refused_dirs: list[Path]) -> Path | None`: uses `shutil.which(name)`. It returns `None` when the result's parent is the CWD or lies inside any of `refused_dirs`.

- [ ] **Step 1: Write failing tests**
  - `test_only_allowed_names_kept`: from a base with `PATH`, `FOO`, `OPENAI_API_KEY`, `GH_TOKEN`, only `PATH` is kept.
  - `test_pass_env_named_key_kept`: `pass_names=['OPENROUTER_API_KEY']` keeps exactly that one.
  - `test_extra_wins_and_is_kept`: `CODEX_HOME` in `extra` is present.
  - `test_secret_like_extra_refused`: `extra={'X_SECRET': '1'}` raises `ValueError`.
  - `test_exe_in_cwd_refused`: a temp dir with a fake `claude.cmd` or `claude` placed first on PATH and set as CWD → `None`.
  - `test_exe_in_project_refused`
  - `test_normal_exe_found`: a temp bin dir not in refused dirs → found.
- [ ] **Step 2:** Run. Expect FAIL.
- [ ] **Step 3: Implement** with case-insensitive name matching on Windows.
- [ ] **Step 4:** Run. Expect PASS.
- [ ] **Step 5: Commit** `feat(run): clean child environment and safe executable lookup`

### Task 4: Launchers for the eight tools

**Files:**
- Create: `hq/run/launchers/__init__.py` (registry), `hq/run/launchers/base.py`, plus one file each: `claude.py`, `codex.py`, `agy.py`, `opencode.py`, `kimi.py`, `gemini.py`, `dsh.py`, `hermes.py`
- Create: `hq/run/wsl.py` (path conversion + stop helper), `hq/run/wsl_wrap.sh`
- Test: `tests/test_run_launchers.py`

**Interfaces:**
- Consumes: `RunRequest` (Task 2), `Profile` (Task 1), `child_env`, `resolve_exe` (Task 3).
- Produces:
  - `Launch(argv: list[str], env: dict[str,str], cwd: Path, stdin_text: str | None, wsl_pidfile: str | None)`
  - `Launcher` protocol:
    - `name: str`, `harness: str` (matching the reader's harness name), `exe: str`
    - `profile_var: str | None` (`'CLAUDE_CONFIG_DIR'`, `'CODEX_HOME'` or `None`)
    - `levels() -> set[str]`, `links_by_stream: bool`
    - `build(req, profile, workdir, run_id, env_base, pass_names) -> Launch`
  - `REGISTRY: dict[str, Launcher]`
  - `available(refused_dirs) -> dict[str, set[str]]`: tool → levels, for tools whose exe resolves. In it, `exe` names the binary inside the tool: Codex has `exe='codex'` but its outer binary is `wsl`.
  - `to_wsl_path(p: Path) -> str`: `C:\Users\Jane Doe\x` → `/mnt/c/Users/Jane Doe/x`.
  - `wsl_stop(pidfile: str)` runs `wsl -d Debian -- /bin/kill -TERM -- -<pgid>`, then KILL after 3 s.

**Level tables.**
- Use spec §2 exactly.
- Every "not offered unless confirmed" cell is absent from `levels()`.
- `full_auto` for Claude, Antigravity, Kimi and Hermes is offered only when `workdir` is a worktree. `build` raises `ValueError` if `level == 'full_auto'` and `workdir` isn't under `<data>/worktrees`; the launcher receives `worktrees_root` in its constructor.

**Arguments**, from spec §2 (prompt placement per tool):
- **Claude:** `claude -p --output-format stream-json --verbose --permission-mode <m> [--model M]`. Prompt on stdin.
- **Codex:** `wsl -d Debian --cd <wslpath> -- bash <wsl path of wsl_wrap.sh> <pidfile> codex exec --json -m <M> -s <sandbox> [-c sandbox_workspace_write.network_access=true] -`. Prompt on stdin. The pidfile is `/tmp/hq-runs/<run_id>.pid`.
  - `wsl_wrap.sh` does `mkdir -p /tmp/hq-runs; echo $$ > "$1"; shift; exec setsid --wait "$@"`.
  - Profile env goes through WSL with `WSLENV`: set `CODEX_HOME` in env and `WSLENV=CODEX_HOME/u`. Don't add `/p`: the profile path is already a Linux path.
- **Antigravity:** `agy -p --output-format stream-json [--model M] [--dangerously-skip-permissions]`. Prompt on stdin with `--input-format text`.
- **opencode:** `opencode run --format json -m <provider/model> -- <prompt>`. Prompt as one argv element after `--`.
- **Kimi:** `kimi --output-format stream-json [-m M] [-y] -p <prompt>`, with the prompt as the single value of `-p`.
- **Gemini:** `gemini [-m M] [--sandbox --yolo] -p <prompt>`.
- **DeepSeek Harness:** `dsh headless -- <prompt>`.
- **Hermes:** `hermes -z <prompt> --in <workdir> -m <M> --usage-file <data>/runs/<id>.usage.json [--yolo]`.
- **Prompt safety:** if any tool's CLI turns out not to accept `--` before a prompt (check `--help` text during implementation), use stdin for that tool instead, and note it in the launcher docstring.

- [ ] **Step 1: Write failing tests**, golden `argv` per tool and level with fixed fake paths:
  - `test_claude_argv_read_only`, `test_claude_argv_edit`, `test_claude_full_auto_requires_worktree`
  - `test_codex_argv_and_wslenv` (profile → `CODEX_HOME` + `WSLENV`)
  - `test_codex_prompt_on_stdin`
  - `test_wsl_path_with_space` (`C:\Users\Jane Doe\ao` → `/mnt/c/Users/Jane Doe/ao`)
  - `test_agy_levels`, `test_opencode_levels_no_full_auto`, `test_kimi_yolo_only_in_worktree`
  - `test_gemini_full_auto_uses_sandbox`, `test_dsh_levels`, `test_hermes_in_dir_always_set`
  - `test_prompt_is_inert`: for every tool, the prompt `"--dangerously-skip-permissions; rm -rf / $(x)\n-y"` appears in argv at most once, as one element after `--` or as the value of `-p`/`-z`, or not at all when on stdin. No other argv element contains it.
  - `test_profile_var_set`: Claude profile → `CLAUDE_CONFIG_DIR`.
  - `test_available_skips_missing_exe`
- [ ] **Step 2:** Run. Expect FAIL.
- [ ] **Step 3: Implement**, one file per tool, each under 80 lines.
- [ ] **Step 4:** Run. Expect PASS.
- [ ] **Step 5: Commit** `feat(run): launchers for eight tools`

### Task 5: Worktrees, diff, merge, discard

**Files:**
- Create: `hq/run/workspace.py`
- Test: `tests/test_run_workspace.py`

**Interfaces:**
- Produces:
  - `is_git_repo(path) -> bool`
  - `create(project: Path, run_id: str, root: Path) -> tuple[Path, str | None, str | None]`, returning (workdir, branch, base_commit). It returns `(project, None, None)` for non-git folders.
  - `diff(project, branch, base_commit, cap=2_000_000) -> tuple[str, bool]`, returning (text, truncated).
  - `merge(project, branch) -> str`, one of `'merged' | 'conflict' | 'dirty'`. It refuses (`'dirty'`) if the project checkout has uncommitted changes.
  - `discard(project, workdir, branch) -> None`
- All git calls use `['git', '-C', str(project), ...]` with a 60 s timeout.
- `merge` runs `git merge --no-edit <branch>` on the project's current branch. On conflict it runs `git merge --abort` and returns `'conflict'`.

- [ ] **Step 1: Write failing tests** (temp git repos made with `git init` and a test user config passed by `-c user.name=t -c user.email=t@t`):
  - `test_create_makes_branch_and_worktree`
  - `test_non_git_returns_project`
  - `test_diff_shows_change`
  - `test_diff_truncated_over_cap`
  - `test_fast_forward_merge`
  - `test_merge_commit_when_diverged`
  - `test_conflict_aborts_and_keeps_branch`: afterwards the project HEAD is unchanged and the branch still exists.
  - `test_dirty_checkout_refused`
  - `test_discard_removes_worktree_and_branch`
  - `test_two_worktrees_same_repo`
  - `test_path_with_space`: the repo lives in a dir named `a b`.
- [ ] **Step 2:** Run. Expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run. Expect PASS.
- [ ] **Step 5: Commit** `feat(run): per-run git worktrees with diff, merge and discard`

### Task 6: Pool pick and guard

**Files:**
- Create: `hq/run/pool.py`, `hq/run/guard.py`
- Test: `tests/test_run_guard.py`

**Interfaces:**
- Consumes: `Profile` (Task 1), `RunRequest` (Task 2); the existing `limit_snapshots` table; `hq.limits.recommend_bridge(pu_dir, task_kind)`.
- Produces:
  - `ProfileState(profile, max_used_percent: float | None, blocked_until: float | None, last_used: float | None)`
  - `profile_states(store, profiles, now) -> list[ProfileState]`: uses the newest snapshot per (account_id, window) for each profile's `account_id`.
  - `pick(states, now) -> Profile | None`, per spec §5.2.
  - `Verdict(allowed: bool, reasons: list[str], warnings: list[str], suggestion: dict | None, profile: str | None, earliest_reset: float | None)`
  - `check(req, store, profiles, recommend: Callable[[str], list | None], now) -> Verdict`
- Rules:
  - `block` refuses when recommend's result has the model with `blocked` true, or every profile is blocked.
  - Any window ≥ 80% → a warning.
  - `warn` never refuses.
  - `auto` replaces tool, model and profile from `recommend(task_kind)[0]` among installed tools. If none is installed, it refuses.

- [ ] **Step 1: Write failing tests**
  - `test_pick_lowest_usage`
  - `test_pick_skips_blocked_until_future`
  - `test_pick_tie_least_recently_used`
  - `test_all_blocked_not_allowed_with_earliest_reset`
  - `test_block_mode_refuses_blocked_model`
  - `test_warn_mode_allows_blocked_with_warning`
  - `test_warning_at_80_percent`
  - `test_auto_mode_fills_from_recommend`
  - `test_auto_mode_no_installed_tool_refused`
  - `test_recommend_unavailable_is_warning_not_crash` (`recommend` returns `None`)
- [ ] **Step 2:** Run. Expect FAIL.
- [ ] **Step 3: Implement.** Mapping a recommend row to a tool: `provider` `codex` → codex, `antigravity` → agy, `hermes` → hermes, `opencode` → opencode. Keep this in one dict in `guard.py`.
- [ ] **Step 4:** Run. Expect PASS.
- [ ] **Step 5: Commit** `feat(run): account pool pick and quota guard`

### Task 7: Runner (processes, stream, stop, limits, recovery)

**Files:**
- Create: `hq/run/proc.py` (platform process control), `hq/run/stream.py`, `hq/run/runner.py`
- Create: `tests/fixtures/run/fake_agent.py` (behaviour chosen by argv: `ok`, `sleep N`, `fail`, `grandchild`, `ratelimit`, `garbage`)
- Test: `tests/test_run_runner.py`, `tests/test_run_stream.py`

**Interfaces:**
- Consumes: `Launch` (Task 4), `Store.*_run` (Task 1), `workspace` (Task 5), `Verdict` (Task 6).
- Produces:
  - `proc.spawn(launch) -> Child`: Windows starts suspended (`CREATE_SUSPENDED | CREATE_NEW_PROCESS_GROUP`), is assigned to a Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, then resumed. POSIX uses `start_new_session=True`.
  - `Child.kill_tree()`, `Child.wait(timeout)`, `Child.stdout` (text lines), `Child.stdin`.
  - `StreamEvent(kind, session_id=None, text=None, rate_limited=False)`, kinds as in spec §3.
  - `parse_line(tool: str, line: str) -> StreamEvent | None`: dispatches to the launcher's `parse`. Lines that aren't JSON give `None` (counted as warnings by the runner). Any line matching `(?i)\b(429|rate.?limit|usage limit|quota)\b` sets `rate_limited`.
  - `Runner(store, run_cfg, launchers, worktrees_root, pu_log: Callable[..., None], on_event: Callable[[dict], None], clock=time.time)`, with:
    - `.start(req, profile) -> dict`: returns the run record, raises `Busy` past `max_concurrent`.
    - `.stop(id)`, `.output(id) -> list[str] | None`, `.shutdown()`, `.recover()`: calls `store.mark_interrupted()`.
  - `pu_log(provider, model, task_kind, outcome, duration_s, note)`: production wiring calls `python <pu_dir>/pu.py log ...` (Task 8). Outcome mapping: done → `ok`, failed/timed_out/interrupted → `error`, rate_limited → `rate_limited`, stopped → `cancelled`.
  - `pu_limit(provider, model, reset_in_h: float | None)`: called once when a run becomes `rate_limited` (production: `pu.py limit <provider> <model> --reason hq-run [--in H]`). The runner also inserts a `limit_snapshots` row for the profile's account (`window='5h'`, `used_percent=100`, `resets_at` = parsed reset or now + 3600, `source='run'`) so `pool.pick` skips that profile until then. The `Runner` constructor gains `pu_limit`.
- Status flow: `queued → starting → running → done|failed|stopped|timed_out|rate_limited`. Exit code 0 → `done`; non-zero → `failed`, unless already stopped, timed out or rate limited.
- The ring is `collections.deque(maxlen=500)`, guarded by the runner lock.
- The time limit uses a `threading.Timer` per run, cancelled on exit.
- The session id from the first `session` event is saved with `update_run(session_id=...)`.
- Each state change calls `on_event({'type': 'run', 'id', 'status'})`.
- `parse` for each launcher is added here, in its launcher file, using recorded samples from Task 11. Until those exist, use the shapes in spec §2 and keep the samples in `tests/fixtures/run/samples/<tool>.jsonl` with invented values.

- [ ] **Step 1: Write failing tests**
  - In `test_run_stream.py`:
    - `test_claude_init_gives_session`, `test_codex_thread_started_gives_session`, `test_kimi_session_event`
    - `test_non_json_is_none`
    - `test_rate_limit_detected`
  - In `test_run_runner.py`, using the fake agent through a test launcher that runs `sys.executable fake_agent.py ...`:
    - `test_ok_run_done_with_session_and_output`
    - `test_fail_run_failed_exit_code`
    - `test_stop_kills_within_5s`
    - `test_stop_during_start`: stop called right after `start` returns `starting` → final status `stopped`, no live process (check with `os.kill(pid, 0)` / `OpenProcess`).
    - `test_time_limit_sets_timed_out` (time limit 1 s via the injectable minimum)
    - `test_grandchild_killed_on_stop`
    - `test_shutdown_kills_grandchild`
    - `test_concurrency_busy`
    - `test_ratelimit_status_and_pu_log_outcome`
    - `test_ratelimit_calls_pu_limit_and_blocks_profile` (afterwards `pool.pick` skips that profile)
    - `test_garbage_lines_kept_and_counted`
    - `test_ring_keeps_last_500`
    - `test_recover_marks_interrupted`
    - `test_prompt_preview_200_chars`
- [ ] **Step 2:** Run. Expect FAIL.
- [ ] **Step 3: Implement.** Windows Job Object via `ctypes.windll.kernel32`: `CreateJobObjectW`, `SetInformationJobObject` (`JobObjectExtendedLimitInformation`, class 9), `AssignProcessToJobObject`, `TerminateJobObject`. Resume the main thread with `NtResumeProcess` from ntdll. Allow a 5 s timeout for the Linux kill; Codex stop also calls `wsl.wsl_stop(pidfile)`.
- [ ] **Step 4:** Run. Expect PASS on Windows. Then also run in WSL Debian from the Linux clone (`/root/agent-hq-work`, Python 3.13) and expect PASS.
- [ ] **Step 5: Commit** `feat(run): runner with process-tree control, stream status and limits`

### Task 8: Server routes and wiring

**Files:**
- Modify: `hq/server.py` (routes), `hq/__main__.py` (build runner, `recover()` at start, `shutdown()` in `finally`)
- Create: `hq/run/service.py` (glue the server calls, keeping `server.py` thin)
- Test: `tests/test_run_api.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `RunService(store, cfg, config_dir, data_dir, recommend)`, with:
    - `.options() -> dict`
    - `.prepare(data) -> dict` (`{verdict, nonce, expires_at}`)
    - `.start(data) -> dict`
    - `.list()`, `.get(id)`, `.output(id)`, `.stop(id)`, `.diff(id)`, `.merge(id)`, `.discard(id)`
  - Each raises `RunError(status: int, message: str)`.
- Routes exactly as spec §6:
  - When `run_mode` is off, everything except `GET /api/runs/options` returns `403 {"error": "run mode is off"}`.
  - `merge` and `discard` require the run to be finished (otherwise `409`).
  - `merge` requires that `diff` was requested for that run at least once; `service` records this per run (otherwise `409 "view the diff first"`).
- `pu_log` wiring runs `[sys.executable, str(pu_dir/'pu.py'), 'log', provider, model, 'hq-run', outcome, '--duration', ...]` with no shell, a 20 s timeout, and failures ignored but counted in health. Here `pu_dir` is the existing provider-usage source path.
- `/api/sessions` and `/api/sessions/{id}` gain `launched_run_id` (a join on `runs.session_id`).

- [ ] **Step 1: Write failing tests** (pattern from `tests/test_server.py`, with a fake launcher registered through `RunService` injection):
  - `test_options_when_off`
  - `test_prepare_403_when_off`
  - `test_start_without_nonce_403`
  - `test_start_replayed_nonce_403`
  - `test_start_tampered_prompt_403`
  - `test_wrong_origin_403`
  - `test_start_ok_201_then_list_and_get`
  - `test_stop_ok`
  - `test_output_ring`
  - `test_output_410_for_run_from_before_restart`
  - `test_full_auto_start_recorded_with_level`
  - `test_untested_tool_hidden_unless_show_untested`
  - `test_merge_before_diff_409`
  - `test_merge_while_running_409`
  - `test_diff_then_merge_ok`
  - `test_sessions_carry_launched_run_id`
  - `test_settings_put_cannot_enable_run_mode`
  - `test_live_feed_gets_run_event`
- [ ] **Step 2:** Run. Expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run the new file, then the full suite. Expect PASS.
- [ ] **Step 5: Commit** `feat(run): run API routes and server wiring`

### Task 9: Session linking fallback and profile session paths

**Files:**
- Create: `hq/run/link.py`
- Modify: `hq/run/runner.py` (periodic link attempt for `links_by_stream=False` tools), `hq/collector.py` or adapter config (extra session paths from profiles)
- Test: `tests/test_run_link.py`

**Interfaces:**
- Produces: `find_session(store, harness, workdir, started_at, window_s=60) -> str | None`. It returns the newest session with `harness`, `project` equal to the workdir (case-insensitive on Windows), and `started_at` within `[run_start, run_start + 60]`.
- The runner tries every 5 s for up to 120 s after start, for tools without a stream session id.
- Profile folders: for each profile of tool `claude` add `<config_dir>/projects` to the `claude_code` source paths. For tool `codex`, add `<config_dir>/sessions` (WSL profiles as `\\wsl.localhost\Debian<config_dir>\sessions`). This happens at load time and in memory only.

- [ ] **Step 1: Write failing tests**
  - `test_link_matches_project_and_window`
  - `test_link_ignores_other_harness`
  - `test_link_ignores_outside_window`
  - `test_link_none_when_absent`
  - `test_profile_paths_added_in_memory_not_saved`
- [ ] **Step 2:** Run. Expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run. Expect PASS.
- [ ] **Step 5: Commit** `feat(run): link runs to sessions by time and project; profile log paths`

### Task 10: Web UI (Run sheet, Runs page, inspector, diff)

**Files:**
- Modify: `web/src/api/types.ts`, `web/src/api/client.ts`, `web/src/api/live.ts`, `web/src/app/router.ts`, `web/src/app/App.tsx`, `web/src/components/Sidebar.tsx`, `web/src/components/Inspector.tsx`, `web/src/pages/Office.tsx`, `web/src/pages/Settings.tsx`
- Create: `web/src/pages/Runs.tsx`, `web/src/pages/runs.css`, `web/src/components/RunSheet.tsx`, `web/src/components/DiffView.tsx`, `web/src/lib/spring.ts`
- Test: `web/src/pages/Runs.test.tsx`, `web/src/components/RunSheet.test.tsx`, `web/src/lib/spring.test.ts`

**Interfaces:**
- Consumes: the API from Task 8.
- Produces:
  - `type Run = {id, tool, model, account, project, level, guard_mode, status, session_id, prompt_preview, started_at, ended_at, exit_code, merge_state}`
  - client functions: `runOptions()`, `prepareRun(body)`, `startRun(body)`, `listRuns()`, `getRun(id)`, `runOutput(id)`, `stopRun(id)`, `runDiff(id)`, `mergeRun(id)`, `discardRun(id)`
  - `spring(from, to, velocity, {damping=1, response=0.35}) -> step(dt) => {value, velocity, done}`

**Design rules:** follow spec §6.1.
- The sheet is translucent over a scrim and springs from the Run button; reduced motion gives a cross-fade, and reduced transparency gives solid surfaces.
- Pressed state on pointer-down.
- Specific verbs: "Start run", "Stop run", "Merge into <branch>", "Discard branch".
- A second confirm only for Full auto, Merge and Discard.
- When `run_mode` is off, the Run button shows a popover: "Run mode is off. Turn it on in config/run.json." No form.

- [ ] **Step 1: Write failing tests**
  - In `RunSheet.test.tsx`:
    - `shows off message when run mode off`
    - `lists only installed tools and their levels`
    - `full auto needs second confirm`
    - `start disabled with reason when verdict not allowed`
    - `shows quota line and suggestion`
    - `start sends prepare then start with nonce`
  - In `Runs.test.tsx`:
    - `lists runs with status pills`
    - `stop button calls stopRun`
    - `diff shown before merge enabled`
    - `merge conflict message`
    - `live run event refreshes row`
  - In `spring.test.ts`:
    - `critically damped settles without overshoot`
    - `interrupt keeps current value`
  - In `Settings.test.tsx`:
    - `run mode card is read-only and links config files`
- [ ] **Step 2:** Run `npm --prefix web test -- --run`. Expect FAIL.
- [ ] **Step 3: Implement.** Add an office badge "launched by HQ" when `launched_run_id` is set, and a Stop button in the Inspector.
- [ ] **Step 4:** Run `npm --prefix web test -- --run` and `npm --prefix web run build`. Expect PASS (106+ tests) and a clean build.
- [ ] **Step 5: Commit** `feat(web): run sheet, runs page, diff review`

### Task 11: Real-tool check, docs, review, release 0.5.0

**Files:**
- Create: `tests/fixtures/run/samples/<tool>.jsonl` (sanitised samples)
- Modify: `hq/run/launchers/*.py` (parse fixes from real samples; hide failing tools), `README.md`, `CHANGELOG.md`, `config/run.json.example`, `config/projects.json.example`
- Vault: `Notes/2026-11-03 Agent HQ run mode.md`

- [ ] **Step 1: Capture samples (main session, not a delegate).** For each installed tool, launch it through `hq.run` once with `level=read_only` in a throwaway git repo under the scratchpad, prompt `List the files in this folder. Do not change anything.`, and time limit 3 min. Save the output ring and replace private text with invented values.
  - Codex: use the default profile only. Agy: skip if provider-usage shows it blocked.
- [ ] **Step 2:** For each tool, confirm:
  - status reaches `done`;
  - a session id is linked (stream or fallback);
  - after one collector pass, that session's token total equals the reader's;
  - no file changed in the repo (`git status --porcelain` empty).
  
  Record a table of results. Tools failing any check get `tested='untested'` and are hidden from `available()` unless `run.json` sets `"show_untested": true`.
- [ ] **Step 3:** Run the full Python and web suites, then `python -m hq --once`. Expect all to pass and readers ok.
- [ ] **Step 4: Independent security review** by a different provider (Codex `gpt-6.1-sol`, high effort, read-only) of the `hq/run/` and `server.py` diff against spec §7. Fix every high or medium finding test-first, then re-check.
- [ ] **Step 5: Docs.**
  - README: run-mode section, config files, levels table, privacy and safety additions.
  - CHANGELOG 0.5.0: Added / Verified (the real-tool table) / Review / Known limits.
  - Vault note, then run `bash "C:/Users/Jane Doe/Documents/Brain/_tools/sync.sh" "Agent HQ run mode (M5a)"`.
- [ ] **Step 6: Commit and merge** `docs(m5a): 0.5.0 run mode`. Merge to `main` and push.
