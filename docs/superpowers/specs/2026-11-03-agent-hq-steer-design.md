# Agent HQ Steer (Milestone 5b): design

Status: draft for review, 2026-11-03. Builds on [run mode M5a](2026-11-03-agent-hq-run-mode-design.md), whose rules all still apply.

## 1. Purpose

Steer lets you talk to an agent that Agent HQ started:
- **Follow-up messages** to a running or idle run.
- **An approvals inbox:** at the new level **Ask me**, the agent's edits and commands wait for your answer.

### Decisions made with the user

| Topic | Decision |
|---|---|
| Scope | Both. Follow-ups for every tested tool; approvals only for tools that can really pause. |
| Waiting | A request waits up to 15 minutes (configurable), then it's denied and the agent is told why. |
| Approval scope | Allow once, or allow for this run (a narrow rule); nothing is remembered after the run. |
| Mechanism | Approach A: answer permission requests over the stream Agent HQ already holds. Nothing is installed in, or written to, tool configs. |

### Success criteria

- A Claude Code **Ask me** run shows each edit or command in the inbox within 2 s. Allow, deny and timeout reach the agent, and the agent continues accordingly.
- A follow-up reaches Claude Code, Codex, opencode and Antigravity on real runs. Each stays one run, with one worktree and branch.
- None of the security tests in section 6 fails, and an independent review finds no open high or medium issue.

## 2. Tool support

Checked from each tool's `--help` on this PC, 2026-11-03.

| Tool | Follow-up | Ask me |
|---|---|---|
| Claude Code | **live**: `--input-format stream-json`; the input stays open | **yes**: `--permission-prompts host` |
| Antigravity | **live**: `--input-format stream-json` | **probe**: offered only if a headless permission request reaches Agent HQ |
| Codex (WSL) | **resume**: `codex exec resume <session>` | **no**: headless Codex can't pause for a person |
| opencode | **resume**: `opencode run -s <session>` | **probe**: offered only if its server mode (`--attach`) exposes permission requests |
| Kimi, DeepSeek Harness, Gemini CLI, Hermes | not offered until their M5a real check passes | no |

Launchers gain three things:
- `steer_mode`: `'live'`, `'resume'` or `None`;
- `can_ask: bool`;
- for live tools, `control_reply(request, decision) -> str`, which formats the line to write on the agent's stdin.

**Probe first (plan task 1).** Capture the real permission-request and reply format from a tiny Claude Code run. Check Antigravity and opencode the same way. Pin the shapes in tests, with invented values. A tool whose probe fails keeps `can_ask = False`.

## 3. Design

### 3.1 Ask me
- A new level, `ask`, listed in the UI between Read-only and Edit project folder. It works in a worktree, like Edit.
- Claude Code gets `--permission-mode manual --permission-prompts host`, so reads go through and edits and commands ask. The exact mode name is pinned by the probe.

### 3.2 Approvals (`hq/run/approvals.py`)
- `Approval(id, run_id, tool_name, summary, requested_at, expires_at, state)`.
  - `state` is `waiting`, `allowed`, `denied`, `expired` or `timed_out`.
  - `summary` is the command or file path, cut to 200 characters.
  - The id is the agent's own request id, namespaced by run.
- An approval is created **only** from a structured control request that the launcher's parser recognises. Agent text never creates one.
- **Answers:**
  - **Allow once**, **Allow for this run** or **Deny**, with an optional note (at most 500 characters) that is passed to the agent on a deny.
  - Each id can be answered once; another answer gets 409.
  - An answer must come for the run that asked.
- **Allow-for-this-run rules** live in memory and end with the run:
  - a command rule matches the same tool name and the same first word of the command;
  - a file rule matches the same tool name and the same parent folder inside the worktree.
  - Never-auto commands always ask: `rm`, `rmdir`, `del`, `git push`, `git reset`, `curl`, `wget`, `ssh`, `scp`, `nc`, `powershell`, `pwsh`, `sudo`. Any command containing `|`, `;`, `&&`, `` ` `` or `$(` also always asks.
- **Timeout:** `approval_timeout_min` in `run.json` (default 15, range 1–120). When it passes, the request is denied with the note "Denied: no answer within N minutes".
- **Run status:** `needs_you` while any request waits. It counts as live for concurrency, and the time limit keeps counting.
- **Database:** a new `approvals` table holds id, run_id, tool_name, summary, requested_at, answered_at and state. No full command beyond the summary.

### 3.3 Follow-ups (`hq/run/followup.py`)
- **Live:** the message is written to the agent's stdin as a user message.
  - Allowed only while the run is idle between turns. Otherwise 409 "the agent is still working".
  - After 30 minutes idle following its last turn, a live run's input is closed; the agent exits and the run becomes `done`.
- **Resume:** the message starts a new process with the run's original level, worktree, profile and environment rules, resuming its session id. The level can never change on resume.
- **After a restart:** live tools resume by session id (`--resume` / `--conversation`) instead of reusing the lost connection.
- **Turns:** every follow-up is a turn. The runs table gains `turns INTEGER`, and the output keeps a turn marker line.
- **Diff and merge** are allowed whenever the run is idle (`idle`, `done`), not only after it ends.

### 3.4 Status flow
`running → idle` (a turn ended and input is open) `→ running` (a follow-up arrived) `→ done`

From `running`, the run can also go to `needs_you` and back. Every other ending is the same as in M5a.

## 4. API

All routes need the token, the run key and a same-origin `Origin`.

| Route | Purpose |
|---|---|
| `GET /api/approvals?state=waiting` | Waiting requests across runs: run id, tool, summary, time left. |
| `POST /api/approvals/{id}` | Body `{decision: 'allow_once' or 'allow_run' or 'deny', note?}`. Returns 409 if already answered and 410 if expired. |
| `POST /api/runs/{id}/message/prepare` | Body `{text}`. Returns a nonce bound to the run id and the text hash. |
| `POST /api/runs/{id}/message` | Body `{text, nonce}`. Returns 202, or 409 while the agent is working. |

The live feed gains `{"type": "approval", "id", "run_id", "state"}` events.

## 5. UI

- **Inbox:**
  - a sidebar item with a counter, and a page listing waiting requests;
  - each request shows the run, tool, summary and a countdown;
  - buttons: **Allow once**, **Allow for this run**, **Deny**, plus a note field.
- **Run detail:**
  - the conversation view: your prompt, the agent's turns, and approvals inline;
  - a message box at the bottom with **Send**, disabled while the agent is working.
- **Office:** a "Needs you" figure of an HQ run opens its inbox item.
- **Notifications:** a browser notification for each new request, if you allow them. It names only the run and the tool.
- **Run sheet:** **Ask me** appears only for tools with `can_ask`.
- The section 6.1 design rules of the M5a spec apply.

## 6. Security

1. Answers and follow-ups need the token, the run key and a same-origin `Origin`. A follow-up also needs a confirm nonce bound to its text.
2. Approvals come only from structured control requests. A line of agent output that looks like a request is ignored and counted as a warning.
3. Request ids are single use and bound to their run. Unknown ids get 404, already-answered ids 409, expired ones 410.
4. Rules are narrow, run-scoped and in memory. The never-auto list and shell operators always ask.
5. A resume reuses the run's level, worktree, profile and environment, so it can't escalate.
6. An unknown control-request type is denied and logged, never allowed.
7. Follow-up text has the same limits as a prompt (100,000 characters, 200-character preview stored).
8. Notifications and the database carry no command beyond the 200-character summary.

## 7. Failures

| Failure | Result |
|---|---|
| Agent exits while a request waits | The request becomes `expired`; answering it gets 410. |
| Stop while a request waits | The request is denied, then the run is stopped. |
| Agent HQ restarts | Waiting requests and live runs become `interrupted`. A follow-up afterwards resumes by session id. |
| A resume fails | The turn is marked `failed` with the last lines shown; the run and branch are kept. |
| Unknown control message | It's denied and logged as a warning. |

## 8. Testing

- **Fake agent** speaking Claude's control format. It covers:
  - allow, deny and timeout;
  - a rule match, and a never-auto command;
  - a shell-operator command;
  - stop while waiting, and exit while waiting;
  - a replayed answer, and an answer to another run's request;
  - agent text that looks like a request.
- **Follow-ups:** the live and resume paths, idle closing, no level change on resume, and 409 while working.
- **API:** run key and nonce on every route, and the cross-run answer test.
- **Real check** (main session):
  - one Claude Code Ask me run, approved once and denied once from the inbox;
  - one follow-up each on Claude Code, Codex, opencode and Antigravity.
- **Review:** an independent security review by a different provider from the implementer.

## 9. Out of scope

- Approvals for sessions that Agent HQ didn't start.
- Saved rules across runs.
- Approvals from another device. Agent HQ stays on `127.0.0.1`; remote access belongs to the remote-hosts design.
