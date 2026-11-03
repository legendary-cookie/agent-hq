# Agent HQ remote watching: design

Status: draft for review, 2026-11-03. Part of 1.0, together with [Steer](2026-11-03-agent-hq-steer-design.md). Remote runs and the optional relay come later, on top of this.

## 1. Purpose

Agent HQ shows agents running on other machines next to the local ones:
- **What:** sessions, tokens, value, limits, and the office.
- **Where:** Linux servers and VMs, other Windows PCs, Macs, and Docker containers on any of those.
- **How the machines are reached:** your own SSH. In the future that's over Tailscale or another VPN, which only changes how the machine is reached. There are no remote machines yet, so the first real test is **WSL Debian reached over SSH**.

### Decisions made with the user

| Topic | Decision |
|---|---|
| Scope | Watch now; remote runs later. All four machine kinds. |
| Connection | System `ssh` with your own config and keys; Tailscale/VPN later. Agent HQ never handles keys or passwords. |
| Approach | A: a read-only scanner sent over SSH; new bytes are mirrored locally; the **existing readers** run unchanged on the mirror. |
| Build | Now, tested end to end against WSL Debian over SSH. |

### Success criteria
- For the Codex sessions in WSL Debian, the remote numbers (sessions, tokens, limit windows) equal what the existing local reader reports over `\\wsl.localhost`. That gives an independent cross-check.
- A host that is unreachable, or that answers with crafted data, can't affect local readers or other hosts, and can't write outside its mirror.
- None of the tests in section 7 fails, and an independent security review finds no open high or medium issue.

## 2. Config: `config/hosts.json`

Agent HQ never writes this file.

```json
{"hosts": [
  {"name": "debian-test", "ssh": "debian-test", "os": "linux", "interval_s": 60,
   "sources": ["claude_code", "codex", "opencode"]},
  {"name": "box1-web", "ssh": "box1", "os": "linux", "container": "web", "sources": ["claude_code"]}
]}
```

| Field | Rule |
|---|---|
| `name` | Must match `^[a-z0-9][a-z0-9-]{0,31}$`. Used in session ids and the mirror path. |
| `ssh` | A `~/.ssh/config` host name or `user@host`, matching `^[A-Za-z0-9][A-Za-z0-9._@-]{0,127}$`. Never starts with `-`. |
| `os` | `linux`, `macos` or `windows`. It picks the scanner command and the default paths. |
| `container` | Optional. Docker container name matching `^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$`. |
| `interval_s` | 30–3600, default 60. |
| `sources` | A subset of the reader names that read files: `claude_code`, `codex`, `opencode`, `antigravity`, `gemini_cli`, `kimi`, `dsh`. Remote paths are the readers' default home-relative paths. |

## 3. Design

### 3.1 Parts
- `hq/remote/config.py`: loads and validates `hosts.json`.
- `hq/remote/scanner.py`: the fixed scanner, sent to the remote machine as text. It's stdlib-only, Python 3.8+, and read-only.
- `hq/remote/ssh.py`: `scan_argv(host) -> list[str]` and `run_scan(host, request) -> dict`.
- `hq/remote/mirror.py`: checks the scanner's answer and writes it under `<data>/remote/<name>/`.
- `hq/remote/collector.py`: one worker thread per host. It runs a scan, writes the mirror, then runs the existing adapters, given source paths into the mirror and `host=name`.

### 3.2 SSH command
- `ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes -o ForwardAgent=no -o ForwardX11=no -o ClearAllForwardings=yes -o ConnectTimeout=10 -- <ssh> <command>`.
- `<command>` is fixed per OS: `python3 -` on linux and macos, `python -` on windows. For a container it's `docker exec -i <container> python3 -`.
- The scanner source plus a JSON request line go over stdin. No shell string is ever built from config values.

### 3.3 Scanner protocol
- **Request:** `{"sources": [...], "cursors": {"<relpath>": [size, mtime]}, "cap_bytes": 20000000, "db_cap_bytes": 200000000}`.
- **What the scanner does:**
  - It walks the source folders under the remote home, resolves links, and skips anything that resolves outside its source folder.
  - It skips the forbidden names: `auth.json`, `.credentials.json`, `credentials`, `secrets`, `.env`, `oauth_creds.json`, `google_accounts.json`.
  - For log files, it returns the bytes after the cursor.
  - For SQLite files, it returns the whole file when size or mtime changed.
  - It stops when the caps are reached.
- **Answer:** one JSON document, `{"files": [{"path", "kind": "append"|"replace"|"reset", "offset", "data_b64", "size", "mtime"}], "more": bool, "errors": [...]}`. Every path is relative to the remote home.

### 3.4 Mirror
- Each returned `path` is checked before writing:
  - relative, with no `..` part, no drive or absolute root, and not forbidden by `is_forbidden`;
  - under one of the requested sources' folders;
  - resolving inside `<data>/remote/<name>/`.
- **Writes:** `append` appends at `offset`, and only when `offset` equals the mirror file's current size. Otherwise the file is re-fetched as `reset`. `replace` writes to a temp file, then renames it.
- The answer is parsed with a 64 MB total limit. Anything over the caps is refused.
- When a host is removed from `hosts.json`, its mirror folder is deleted at the next start.

### 3.5 Data and UI
- `sessions` gains `host TEXT`: null for local, the host name for remote. A remote session id is `<host>/<harness>:<id>`. Limits are recorded with that host.
- **UI:**
  - a host filter on Agents and Dashboard, and a host badge in the inspector;
  - in the office, one zone per host (isometric and top-down) or one floor per host (tower);
  - in Settings, each host's health: last pass, bytes read, catching up, and the last error.
- Run mode is unchanged: remote sessions are watch-only.

## 4. Security
1. Only hosts listed in `hosts.json`, with validated names. The fixed ssh options, and `--` before the destination.
2. Agent HQ never reads, copies or passes keys, passwords or ssh-agent state. It relies on your ssh setup.
3. Everything the host sends back is untrusted. Paths are checked, sizes are capped, and the data is parsed only by the existing readers.
4. The scanner only reads, and never runs anything it finds. The forbidden names are skipped on both sides.
5. A failing or hostile host affects only its own mirror and health row.

## 5. Failures

| Failure | Result |
|---|---|
| Unreachable host, host key not known or changed, no Python | The error shows in health; the host backs off (doubling, up to 10 min); other hosts and local readers keep working. |
| Caps reached | `more: true`; it continues next pass, and health says "catching up". |
| Remote file shrank or rotated | The scanner sends `reset`; the mirror file is rewritten and the reader's cursor is reset. |
| Malformed answer | The pass is discarded and logged in health; the mirror is unchanged. |

## 6. Test machine: WSL Debian over SSH
- **Setup steps for the user** (given in the plan):
  - install `openssh-server` in Debian, listening on `127.0.0.1:2222` only;
  - add your public key to `/root/.ssh/authorized_keys`;
  - add a `debian-test` entry to `~/.ssh/config`;
  - accept the host key once.
- **Cross-check:** the remote watch of `debian-test` reads `/root/.codex/sessions`, and its session count and token totals must equal the local WSL reader's (`\\wsl.localhost\Debian\root\.codex\sessions`) for the same sessions.

## 7. Testing
- **Scanner unit tests** on temp folders: forbidden names, symlinks leading outside a source, caps, cursors, rotation, and an SQLite replace.
- **Mirror tests** with crafted answers: `..`, absolute and drive paths, forbidden names, oversized data, offset mismatch, invalid base64.
- **SSH argv:** golden tests, plus refusal of bad `ssh`, `name` and `container` values.
- **Collector:** one host failing doesn't stop another; backoff; adapters get `host` and the mirror paths.
- **End to end** against WSL Debian over SSH, with the section 6 cross-check.
- **Review:** an independent security review.

## 8. Out of scope
- Remote runs and the relay.
- Password logins and key management.
- Watching Kubernetes pods.
