# Agent HQ Remote Watching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show agents on other machines (Linux, Windows, macOS, Docker) next to local ones, read over the user's own SSH.

**Architecture:**
- **Scanner.** A fixed, read-only Python scanner goes over `ssh <host> python3 -`. It returns only new bytes of agent log files.
- **Mirror.** Agent HQ checks every path in the answer, then appends the bytes to a local mirror under `<data>/remote/<host>/`.
- **Readers.** The existing adapters run unchanged on the mirror. Their batch is namespaced with the host before it is stored.

**Tech Stack:** Python 3.11+ stdlib (scanner: Python 3.8+ stdlib), SQLite, existing React UI.

**Spec:** [docs/superpowers/specs/2026-11-03-agent-hq-remote-watch-design.md](../specs/2026-11-03-agent-hq-remote-watch-design.md)

## Global Constraints

- Stdlib only, no new npm dependencies, never `npm install`. `config/hosts.json` is never written by Agent HQ.
- **SSH argv, exactly:**
  `['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ForwardAgent=no', '-o', 'ForwardX11=no', '-o', 'ClearAllForwardings=yes', '-o', 'ConnectTimeout=10', '--', <ssh>, *command]`
  - `command` is `['python3', '-']` on linux and macos, or `['python', '-']` on windows.
  - With a container it is `['docker', 'exec', '-i', <container>, 'python3', '-']`.
- **Validation patterns:**
  - name: `^[a-z0-9][a-z0-9-]{0,31}$`
  - ssh: `^[A-Za-z0-9][A-Za-z0-9._@-]{0,127}$`
  - container: `^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$`
  - `interval_s`: 30–3600, default 60
  - `os`: `linux`, `macos` or `windows`
- **Caps:** 20 MB of log bytes per pass; 200 MB per SQLite file; 64 MB answer limit; ssh timeout 120 s per pass.
- **Forbidden names, checked on both sides:** `auth.json`, `.credentials.json`, `credentials`, `secrets`, `.env`, `oauth_creds.json`, `google_accounts.json`. The Agent HQ side uses `hq.config.is_forbidden` as well.
- **Remote source paths**, relative to the remote home:

  | Source | Path |
  |---|---|
  | `claude_code` | `.claude/projects` |
  | `codex` | `.codex/sessions` |
  | `opencode` | `.local/share/opencode/opencode.db` |
  | `antigravity` | `.gemini/antigravity-cli/conversations` and `.gemini/antigravity-cli/history.jsonl` |
  | `gemini_cli` | `.gemini/tmp` |
  | `kimi` | `.kimi-code/sessions` and `.kimi/sessions` |
  | `dsh` | `.dsh/sessions` |

- **Remote session ids** are `<host>/<local id>`. That covers session ids, event session ids and parent ids. `sessions.host` is the host name.
- Tests never use the network, and never touch `~/.ssh`. SSH is replaced by a fake runner that executes the scanner locally on temp folders.

## Review Focus

1. **A hostile answer path**, for example `.claude/projects/../../../Windows/x`, `C:\x`, `\\server\share`, `.codex/sessions/auth.json`, or a path inside a different source than requested. It must be refused, with nothing written. Pinned in Task 3: `test_hostile_paths_refused`.
2. **The scanner following a symlink out of a source**, such as `.claude/projects/link -> /etc`. It must skip the link. Pinned in Task 2: `test_symlink_out_of_source_skipped`.
3. **An append whose offset doesn't match the mirror.** For example, the remote file was truncated and then grew again. The mirror must reset that file and never write at the wrong offset. Pinned in Task 3: `test_offset_mismatch_resets`.
4. **One bad host stalling the others.** A host that hangs until the ssh timeout must not delay the others, or the local collector. Pinned in Task 4: `test_hung_host_does_not_block_others`.
5. **Remote and local sessions with the same id.** For example, the same Codex session copied to both machines. They must stay two separate sessions. Pinned in Task 4: `test_remote_ids_never_collide_with_local`.

---

### Task 1: Hosts config and SSH command

**Files:**
- Create: `hq/remote/__init__.py`, `hq/remote/config.py`, `hq/remote/ssh.py`
- Test: `tests/test_remote_config.py`

**Interfaces:**
- `Host(name, ssh, os, container: str | None, interval_s: int, sources: tuple[str, ...])`
- `load_hosts(config_dir: Path) -> tuple[list[Host], list[str]]`: returns the hosts and warnings. A missing file means `[]`. An invalid entry is skipped with a warning, and never raises for the whole file.
- `scan_argv(host: Host) -> list[str]`: exactly the Global Constraints shape.
- `run_scan(host, request: dict, scanner_text: str, runner=subprocess.run) -> dict`
  - stdin is `scanner_text + '\n__HQ_REQUEST__\n' + json.dumps(request)`.
  - Raises `RemoteError(message)` on a non-zero exit, a timeout, more than 64 MB of stdout, or invalid JSON.
  - The ssh stderr is cut to 300 characters in the message.

- [ ] **Step 1: Write failing tests.**
  - `test_load_valid_hosts`, `test_invalid_entries_skipped_with_warning`, covering a leading `-`, spaces, `;`, uppercase in the name, and an unknown source
  - `test_scan_argv_linux_exact`, `test_scan_argv_windows`, `test_scan_argv_container`
  - `test_run_scan_errors`, covering exit 255, a timeout, oversized output and bad JSON, all through a fake runner
- [ ] **Step 2:** Run `python -m unittest tests.test_remote_config -v`. Expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run it again. Expect PASS.
- [ ] **Step 5: Commit** `feat(remote): hosts config and fixed ssh command`

### Task 2: Scanner

**Files:**
- Create: `hq/remote/scanner.py`. It must be standalone: no `hq` imports, stdlib only, and Python 3.8 syntax.
- Test: `tests/test_remote_scanner.py`. The tests run the scanner as `python scanner.py` with a stdin request and `HOME` / `USERPROFILE` pointing at a temp folder.

**Interfaces:**
- **Input:** the request JSON after the marker line, `{"sources": [...], "cursors": {relpath: [size, mtime]}, "cap_bytes": int, "db_cap_bytes": int}`.
- **Output** is one JSON document on stdout: `{"files": [...], "more": bool, "errors": [...]}`. Each file entry is `{"path": relposix, "kind": "append"|"replace"|"reset", "offset": int, "data_b64": str, "size": int, "mtime": float}`.
- **Rules:**
  - Walk only the Global Constraints paths for the requested sources, under `Path.home()`.
  - Each candidate goes through `os.path.realpath`, and is skipped if it ends up outside the source root.
  - Skip forbidden names, both as files and as directories.
  - `.db` files are sent whole (`replace`) when size or mtime changed, and skipped above `db_cap_bytes`.
  - Other files:
    - `append` from the cursor size;
    - `reset` (from 0) when the current size is smaller than the cursor, or the mtime went backwards while the size is unchanged.
  - Stop adding files when `cap_bytes` is reached, and set `more: true`.
  - The scanner never executes, imports or opens anything outside these reads.

- [ ] **Step 1: Write failing tests.**
  - `test_new_file_sent_append_from_zero`
  - `test_cursor_sends_only_new_bytes`
  - `test_shrunk_file_reset`
  - `test_forbidden_names_skipped`
  - `test_symlink_out_of_source_skipped` (skipped on Windows if a symlink can't be created)
  - `test_cap_sets_more`
  - `test_db_replace_on_change`, `test_db_over_cap_skipped`
  - `test_unrequested_source_not_read`
  - `test_runs_on_python38_syntax`: compile with `ast.parse(..., feature_version=(3, 8))`
- [ ] **Step 2:** Run the tests. Expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run them again. Expect PASS.
- [ ] **Step 5: Commit** `feat(remote): read-only log scanner`

### Task 3: Mirror writer

**Files:**
- Create: `hq/remote/mirror.py`
- Test: `tests/test_remote_mirror.py`

**Interfaces:**
- `apply_answer(answer: dict, host: Host, root: Path) -> MirrorResult(written: int, bytes: int, resets: int, more: bool, errors: list[str])`
  - `root` is `<data>/remote/<host.name>`.
- **Validation per entry:**
  - `path` is a string;
  - it has no `..` parts, no drive, no root, no backslash, and no NUL;
  - it starts with one of the host's requested source paths;
  - `is_forbidden` is false;
  - the resolved target stays inside `root`;
  - the base64 is valid;
  - `kind` is a known value.

  An invalid entry is skipped and counted in `errors`. A whole answer that isn't a dict is refused.
- **Writes:**
  - `append` only when `offset` equals the current mirror size; otherwise record a reset request (`cursors` drop that path, so the next pass sends a `reset`).
  - `reset` and `replace` write a temp file in the same folder, then `os.replace`.
- `cursors_for(root, host) -> dict`: builds the next request's cursors from the mirror files, as size plus the stored remote mtime. The mtime is kept in a `.hqmeta.json` file per host root.
- `prune_hosts(remote_root, keep: set[str])`: deletes the mirror folders of removed hosts.

- [ ] **Step 1: Write failing tests.**
  - `test_append_and_cursor_roundtrip`
  - `test_hostile_paths_refused`, covering `..`, an absolute path, a drive path, UNC, a backslash, a forbidden name, an unrequested source, NUL and bad base64
  - `test_offset_mismatch_resets`
  - `test_replace_is_atomic` (no partial file after a simulated crash between the write and the rename)
  - `test_prune_removed_hosts`
- [ ] **Step 2:** Run the tests. Expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run them again. Expect PASS.
- [ ] **Step 5: Commit** `feat(remote): validated local mirror`

### Task 4: Remote collector and namespacing

**Files:**
- Create: `hq/remote/collector.py`, `hq/remote/namespace.py`
- Modify: `hq/store.py` (`sessions.host`, an `apply(..., host=None)` parameter); `hq/__main__.py` (start the remote collector; `--once` runs one remote pass too); `hq/server.py` (health includes hosts; `/api/sessions` gets a `host` filter and returns `host`)
- Test: `tests/test_remote_collector.py`

**Interfaces:**
- `prefix_batch(batch: Batch, host: str) -> Batch`:
  - every session id, event session id and parent id becomes `f'{host}/{id}'`;
  - limits keep their account;
  - `source_ref` stays as the mirror path.
- `RemoteCollector(store, cfg, hosts, data_dir, adapter_factory, run_scan=ssh.run_scan, scanner_text=None, clock=time.time)`:
  - one daemon thread per host;
  - each pass: `cursors_for` → `run_scan` → `apply_answer` → run a **fresh adapter set for this host**, built once per host by `adapter_factory(cfg, data_dir)` (the same `build_adapters`), with `SourceCfg(True, [mirror paths])`;
  - then `prefix_batch`, then `store.apply(batch, account_of, cfg.relabel, host=name)`;
  - on a failure, the backoff doubles each time up to 600 s, and resets after a success;
  - `health() -> {name: {state, last_pass, bytes, catching_up, error}}`.
  - Adapters that don't read files (`deepseek_balance`, `importer`, `provider_usage`, `zylos`) are never built for a host.
- `store.apply(..., host=None)` writes `sessions.host`. A host's sessions only update rows with the same host.

- [ ] **Step 1: Write failing tests.** Use a fake `run_scan` that runs the real scanner against a temp HOME holding Codex and Claude fixture logs copied from the existing reader tests.
  - `test_remote_pass_stores_namespaced_sessions`
  - `test_remote_numbers_equal_local_reader`: the same fixture read locally and remotely gives the same token totals
  - `test_remote_ids_never_collide_with_local`
  - `test_hung_host_does_not_block_others`
  - `test_backoff_and_recovery`
  - `test_health_reports_errors`
  - `test_once_includes_remote_pass`
- [ ] **Step 2:** Run the tests. Expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run them, then the full suite on Windows. Expect PASS.
- [ ] **Step 5: Commit** `feat(remote): per-host collector with namespaced sessions`

### Task 5: UI

**Files:**
- Modify: `web/src/api/types.ts`, `web/src/pages/Agents.tsx`, `web/src/pages/Dashboard.tsx`, `web/src/components/Inspector.tsx`, `web/src/pages/Settings.tsx`, and the `web/src/office/` scene (zones and floors per host)
- Test: next to each page

**Behaviour:**
- **Agents and Dashboard:** a host filter, with "This PC" plus each host.
- **Inspector:** a host badge.
- **Office:** one zone per host (isometric and top-down) or one floor per host (tower). Local stays as it is.
- **Settings:** a hosts card per host showing last pass, bytes read, "catching up" and the last error, plus the text "Hosts are listed in config/hosts.json."

- [ ] **Step 1: Write failing tests.**
  - `host filter narrows sessions`
  - `inspector shows host badge`
  - `office groups remote agents by host`
  - `settings hosts card shows error`
- [ ] **Step 2:** Run `npm --prefix web test -- --run`. Expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run the tests and `npm --prefix web run build`. Expect PASS.
- [ ] **Step 5: Commit** `feat(web): remote hosts in agents, office and settings`

### Task 6: WSL test machine, real check, review, docs (main session, with the user)

- [ ] **Step 1: User setup.** The main session gives the user these commands:
  - in Debian, install `openssh-server`;
  - set `ListenAddress 127.0.0.1`, `Port 2222` and `PasswordAuthentication no`;
  - put the user's own public key in `/root/.ssh/authorized_keys`;
  - start sshd;
  - add a `Host debian-test` entry (`HostName 127.0.0.1`, `Port 2222`, `User root`) to `~/.ssh/config`;
  - run `ssh debian-test true` once to accept the host key.
- [ ] **Step 2:** Add `debian-test` to a scratch `config/hosts.json` (sources `codex`, `claude_code`), then run `python -m hq --once`. Expect the host to show `ok`.
- [ ] **Step 3: Cross-check.** For every Codex session in Debian, `debian-test/codex:<id>` must have the same token totals as the local WSL reader's `codex:<id>`. Use an independent script that compares the two in the database. Expect 0 mismatches.
- [ ] **Step 4: Independent security review** by a different provider. Focus on spec §4 and the Review Focus. Fix high and medium findings test-first, then re-check.
- [ ] **Step 5: Docs.** A README remote hosts section, including the Tailscale note: an ssh host name is all that's needed. CHANGELOG 1.0.0, together with Steer. A vault note, then sync.
- [ ] **Step 6:** Merge to `main` and push.
