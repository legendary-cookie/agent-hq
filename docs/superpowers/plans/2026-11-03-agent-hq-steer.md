# Agent HQ Steer (M5b) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Follow-up messages to runs Agent HQ started, and an approvals inbox for the new **Ask me** level.

**Architecture:**
- Live tools keep stdin open. Approvals arrive as structured control requests on the agent's output, and Agent HQ answers them on its input.
- Resume tools get a new process per follow-up. It uses the run's frozen launch settings.
- Approvals live in memory, with a summary row in SQLite.

**Tech Stack:** Python 3.11+ stdlib, SQLite; React 18.3.1 + TS + Vite 5.4.11 (existing `web/`).

**Spec:** [docs/superpowers/specs/2026-11-03-agent-hq-steer-design.md](../specs/2026-11-03-agent-hq-steer-design.md). Read it, and the M5a spec §7 security rules, before every task.

## Global Constraints

- All M5a constraints still apply. See [the M5a plan](2026-11-03-agent-hq-run-mode.md#global-constraints): stdlib only; no shell; no npm install; tests never run real agent tools.
- **Claude Code permission protocol, from a real probe on 2026-11-03.** Do not change the exact strings.
  - Launch flags: `--input-format stream-json --output-format stream-json --verbose --permission-mode default --permission-prompts host --permission-prompt-tool stdio`.
  - First user message on stdin: `{"type":"user","message":{"role":"user","content":<text>}}`.
  - Request (stdout):
    ```json
    {"type":"control_request","request_id":R,"request":{"subtype":"can_use_tool","tool_name":T,"input":{...},"tool_use_id":U,"description":D,"permission_suggestions":[...]}}
    ```
  - Deny (stdin):
    ```json
    {"type":"control_response","response":{"subtype":"success","request_id":R,"response":{"behavior":"deny","message":M}}}
    ```
  - Allow (stdin): the same envelope, with `"response":{"behavior":"allow","updatedInput":<the request's input, unchanged>}`.
- **Timeouts:** `approval_timeout_min` defaults to 15 (range 1–120), and is set in `run.json`. Live idle close is 30 min.
- **Never-auto** (always ask, even with an allow-for-this-run rule):
  - command first words: `rm`, `rmdir`, `del`, `git push`, `git reset`, `curl`, `wget`, `ssh`, `scp`, `nc`, `powershell`, `pwsh`, `sudo`;
  - any command containing `|`, `;`, `&&`, `` ` `` or `$(`.
- **Size limits:** follow-up text up to 100,000 characters; deny note up to 500; approval summary up to 200 characters.
- **Auth:** every new route needs the token, `X-HQ-Run-Key` and a same-origin `Origin`.

## Review Focus

1. **A fake permission request in agent text.** A `control_request` JSON inside an assistant message, or inside a tool result, must not create an approval. Only top-level stdout lines of type `control_request` count. Pinned in Task 2: `test_control_request_inside_text_is_ignored`.
2. **An answer racing the timeout.** You click Allow at the same moment the 15-minute timer fires. Exactly one reply must reach the agent, and the state must match it. Pinned in Task 1: `test_answer_and_timeout_race_single_reply`.
3. **Rule bypass by command shape.** `rm` behind `npm test && rm -rf x`, `git  push` with extra spaces, `"rm"` in quotes, or a full path `/bin/rm` must still ask. Pinned in Task 1: `test_never_auto_shapes`.
4. **A follow-up while the agent is mid-turn.** It must be refused (409). It must not be queued and then written halfway through a control reply. Pinned in Task 3: `test_followup_while_working_refused`.
5. **A resume that escalates.** A follow-up on a Read-only Codex run must still run `-s read-only`, even if the run's record was edited in the DB. The launch settings are frozen in memory at start and in a separate column. Pinned in Task 3: `test_resume_keeps_frozen_level`.

---

### Task 1: Approvals core

**Files:**
- Create: `hq/run/approvals.py`
- Modify: `hq/store.py` (an `approvals` table, and `runs.turns`); `hq/run/config.py` (`approval_timeout_min`)
- Test: `tests/test_run_approvals.py`

**Interfaces:**
- `Approval(id: str, run_id: str, tool_name: str, summary: str, requested_at: float, expires_at: float, state: str)`
- `ApprovalBook(store, timeout_s: float, on_change: Callable[[dict], None], clock=time.time)`, with:
  - `.open(run_id, request_id, tool_name, tool_input: dict, reply: Callable[[str, str | None], None]) -> Approval | None`
    - It returns None when a run rule auto-allows the request; in that case it calls `reply('allow', None)` at once.
  - `.answer(approval_id, decision: str, note: str | None) -> Approval`
    - `decision` is `allow_once`, `allow_run` or `deny`.
    - It raises `KeyError` (unknown id), `AnsweredError` (409) or `ExpiredError` (410).
  - `.close_run(run_id, state='expired')`, `.waiting(run_id=None) -> list[Approval]`, `.has_waiting(run_id) -> bool`.
- `summarize(tool_name, tool_input) -> str`: the command for Bash/PowerShell, the `file_path` for Write/Edit, otherwise the tool name plus its first input value. At most 200 characters.
- `never_auto(tool_name, tool_input) -> bool` and `rule_key(tool_name, tool_input, worktree: Path) -> str | None`.
  - A command rule key is the tool plus the first word, with the path stripped and lower-cased.
  - A file rule key is the tool plus the parent folder, relative to the worktree.
  - It returns None when the request is never-auto, or when the path is outside the worktree.
- The approval id is `f'{run_id}:{request_id}'`. The timer is a `threading.Timer`. Every decision goes through one lock, and `reply` is called at most once per approval.

- [ ] **Step 1: Write failing tests.**
  - `test_open_creates_waiting_and_db_row`
  - `test_allow_once_replies_allow`
  - `test_deny_with_note_replies_deny_note`
  - `test_allow_run_rule_auto_allows_next_matching`
  - `test_rule_does_not_cross_runs`
  - `test_never_auto_shapes`, covering `npm test && rm -rf x`, `git  push`, `"rm" -f a`, `/bin/rm a`, `echo $(whoami)` and `curl x | sh`
  - `test_file_rule_outside_worktree_none`
  - `test_timeout_denies_with_message`
  - `test_answer_and_timeout_race_single_reply`: a fake clock plus a reply counter
  - `test_answer_twice_409`, `test_answer_expired_410`, `test_close_run_expires_waiting`
  - `test_summary_capped_200`, `test_note_capped_500`
- [ ] **Step 2:** Run `python -m unittest tests.test_run_approvals -v`. Expect FAIL.
- [ ] **Step 3: Implement.** The `approvals` table holds id, run_id, tool_name, summary, requested_at, answered_at and state, and gets an ALTER-style migration like `base_commit`.
- [ ] **Step 4:** Run the tests again, then the full suite. Expect PASS.
- [ ] **Step 5: Commit** `feat(steer): approvals core with run rules and timeout`

### Task 2: Live launchers and control messages

**Files:**
- Modify: `hq/run/launchers/base.py`, `claude.py`, `agy.py`, `codex.py` and `opencode.py`; `hq/run/stream.py`; `hq/run/request.py` (the `ask` level)
- Test: `tests/test_run_steer_launchers.py`

**Interfaces:**
- `BaseLauncher` gains:
  - `steer_mode: str | None = None` (`'live'` or `'resume'`), `can_ask = False`;
  - `first_input(prompt) -> str`: the stdin text for live tools;
  - `user_message(text) -> str`;
  - `control_reply(request_id, decision, note, tool_input) -> str`;
  - `build_resume(req, profile, workdir, run_id, session_id, text, env_base, pass_names) -> Launch`: raises `NotImplementedError` for non-resume tools.
- **Claude:** `steer_mode='live'` and `can_ask=True`. Level `ask` uses exactly the protocol flags in Global Constraints, and stdin stays open. The other levels keep their M5a flags plus `--input-format stream-json`, with the first message sent as stream-json.
- **Antigravity:** `steer_mode='live'`, using `--input-format stream-json`, and `can_ask=False` until the Task 6 probe.
- **Codex:** `steer_mode='resume'`. `build_resume` runs `codex exec resume <id> --json -s <frozen sandbox> -`, with the text on stdin and the same `wsl --exec` wrapper.
- **opencode:** `steer_mode='resume'`. `build_resume` runs `opencode run --format json -s <id> -m <model> -- <text>`.
- `StreamEvent` gains the kinds `permission` (with `.request: dict`, holding `request_id`, `tool_name`, `input` and `tool_use_id`) and `turn_end`. `turn_end` comes from Claude's `result` event, Antigravity's `result` event, and Codex's `turn.completed`.
- Only a top-level line whose `type` is `control_request` with `request.subtype == 'can_use_tool'` becomes a `permission` event. Any other `control_request` subtype gives `StreamEvent('control_unknown')`.
- Session ids are the raw stream ids; `parse_line` still adds the harness prefix. A resume needs the raw id, so the runner keeps both.

- [ ] **Step 1: Write failing tests.**
  - `test_claude_ask_flags_exact`
  - `test_claude_first_input_is_stream_json_user`
  - `test_control_reply_allow_and_deny_exact_json`: byte-for-byte against the Global Constraints shapes
  - `test_permission_event_parsed`
  - `test_control_request_inside_text_is_ignored`
  - `test_unknown_control_subtype`
  - `test_codex_resume_argv_keeps_sandbox`
  - `test_opencode_resume_argv`
  - `test_turn_end_events`
  - `test_ask_level_only_for_can_ask_tools`
- [ ] **Step 2:** Run the tests. Expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run the tests, then the full suite. Expect PASS. Update any M5a golden argv tests that change because Claude now uses stream-json input.
- [ ] **Step 5: Commit** `feat(steer): live launch, control messages and resume argv`

### Task 3: Runner, live input and follow-ups

**Files:**
- Modify: `hq/run/runner.py`
- Create: `hq/run/followup.py`
- Test: `tests/test_run_steer_runner.py`; extend `tests/fixtures/run/fake_agent.py`

**Interfaces:**
- `Runner(..., approvals: ApprovalBook, idle_close_s=1800.0)`.
- **Live runs:**
  - the runner writes `first_input`, then keeps stdin open;
  - `permission` events go to `approvals.open(..., reply=...)`, where `reply` writes `control_reply` under a per-run write lock;
  - a `turn_end` event sets status `idle` and starts the idle-close timer;
  - while any approval waits, status is `needs_you`.
- `Runner.send(rid, text) -> None`:
  - It raises `NotIdle` (409) unless the status is `idle` or `done`, or the run is a resume run that has finished.
  - **Live:** it writes `user_message`, bumps `turns`, and sets the status to `running`.
  - **Resume:** it calls `followup.resume_launch(...)` with the **frozen** launch settings (tool, level, model, profile, workdir, env names) captured at `start`, never re-read from the DB. Then it spawns through the same `proc.spawn` and reader. Same run id, `turns += 1`.
- Stop while waiting runs `approvals.close_run(rid, 'denied')` first, then the existing stop.
- An exit with approvals waiting runs `close_run(rid, 'expired')`.
- A `control_unknown` event writes a deny reply, using the request id when present, and counts a warning.
- **The fake agent** gets modes:
  - `ask <tool> <command>`: prints a `can_use_tool` control request, waits for the reply on stdin, and prints `{"type":"tool_done","behavior":...}`;
  - `live`: after its first turn, prints `result` and waits for further user messages.

- [ ] **Step 1: Write failing tests.**
  - `test_ask_allow_reaches_agent`, `test_ask_deny_reaches_agent`, `test_ask_timeout_denies`
  - `test_status_needs_you_then_running`
  - `test_live_turn_end_sets_idle`, `test_send_live_followup`
  - `test_followup_while_working_refused`
  - `test_resume_keeps_frozen_level`: edit `runs.level` in the DB first, then assert the resume argv still uses the original level
  - `test_stop_while_waiting_denies_then_stops`, `test_exit_while_waiting_expires`
  - `test_unknown_control_denied`
  - `test_idle_close_ends_live_run`
- [ ] **Step 2:** Run the tests. Expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run the tests and the full suite on Windows. Expect PASS.
- [ ] **Step 5: Commit** `feat(steer): runner handles approvals, idle turns and follow-ups`

### Task 4: API

**Files:**
- Modify: `hq/run/service.py`, `hq/server.py`
- Test: `tests/test_run_steer_api.py`

**Interfaces:**
- `RunService.approvals_list(state)`, `.answer(aid, data)`, `.message_prepare(rid, data)` and `.message(rid, data)`, with the routes exactly as in spec §4.
  - The message nonce is bound to `(rid, sha256(text))`, using the existing `NonceBook` with a request-like key.
- Diff and merge are allowed when the status is `idle` or a finished one.
- The live feed gets `approval` events, from the `ApprovalBook.on_change` hook through the existing notifier.

- [ ] **Step 1: Write failing tests.**
  - `test_answer_requires_run_key`
  - `test_answer_other_runs_request_404`
  - `test_answer_twice_409`
  - `test_message_requires_nonce_bound_to_text`
  - `test_message_while_working_409`
  - `test_diff_allowed_when_idle`
  - `test_feed_gets_approval_event`
- [ ] **Step 2:** Run the tests. Expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run the tests on Windows, where sockets work. Expect PASS.
- [ ] **Step 5: Commit** `feat(steer): approvals and message API`

### Task 5: Web UI

**Files:**
- Modify: `web/src/api/*`, `web/src/app/router.ts`, `web/src/components/Sidebar.tsx`, `web/src/components/RunSheet.tsx`, `web/src/pages/Runs.tsx`, `web/src/pages/Office.tsx`
- Create: `web/src/pages/Inbox.tsx`, `web/src/pages/inbox.css`, `web/src/components/Conversation.tsx`
- Test: `web/src/pages/Inbox.test.tsx`, `web/src/components/Conversation.test.tsx`

**Screens (spec §5):**
- **Inbox:** a sidebar counter; rows with a countdown; **Allow once**, **Allow for this run** and **Deny**, plus a note field.
- **Conversation view** in the run detail: turn markers, inline approvals, and a message box. **Send** is disabled unless the run is idle or done. Sending calls `message/prepare`, then `message`.
- **Office:** a "Needs you" figure of an HQ run links to its inbox item.
- **Notifications:** a browser Notification with only the run and tool, after you allow them from a button.
- **Run sheet:** "Ask me" is listed only where `can_ask` is set.

- [ ] **Step 1: Write failing tests.**
  - `inbox lists waiting with countdown`
  - `allow once posts decision`
  - `deny sends note`
  - `send disabled while working`
  - `send prepares then posts`
  - `ask me only for can_ask tools`
  - `notification text has no command`
- [ ] **Step 2:** Run `npm --prefix web test -- --run`. Expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run `npm --prefix web test -- --run` and `npm --prefix web run build`. Expect PASS.
- [ ] **Step 5: Commit** `feat(web): approvals inbox and conversation view`

### Task 6: Probes, real check, review, docs (main session)

- [ ] **Step 1: Probe Antigravity and opencode** for headless permission requests, with one tiny run each in the scratch repo.
  - Antigravity: `--input-format stream-json`, asking it to write a file without `--dangerously-skip-permissions`; watch for an approval-shaped event.
  - opencode: `--attach` to a local `opencode serve` on 127.0.0.1, then look for its permission event or endpoint.
  - Set `can_ask = True` only where a real deny reply blocked the write. Pin the shapes in tests.
- [ ] **Step 2: Real check through the UI.**
  - A Claude Code Ask me run: allow one write and deny one command from the inbox. Confirm the file exists and the command didn't run.
  - One follow-up each on Claude Code, Codex, opencode and Antigravity. Confirm each linked session shows the extra turn.
- [ ] **Step 3: Independent security review** by a different provider than the implementer. Focus on spec §6 and the Review Focus above. Fix high and medium findings test-first, then re-check.
- [ ] **Step 4: Docs.** README Steer section; CHANGELOG 0.6.0; vault note; then run `bash "C:/Users/Jane Doe/Documents/Brain/_tools/sync.sh" "Agent HQ Steer (M5b)"`.
- [ ] **Step 5:** Merge to `main` and push.
