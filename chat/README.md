# Agent HQ Chat (optional companion)

Chat, channels, a task board and an agent directory adapted from the original private fleet app. Includes attachments, Markdown, search, streaming replies, agent switching, exports (Markdown/text/JSON), live activity and reply/quote. Voice/Talk mode is experimental and depends on browser speech support.

HQ remains a standard-library-only app. Chat is a separate optional FastAPI service with its own SQLite database and accounts. This is not shared sign-in: create Chat users separately and assign agent ownership/permissions in Chat Admin.

## Run from the repository root

Use Python 3.11+ and Node 20+. Install optional dependencies and build:

```sh
python -m pip install -r chat/server/requirements.txt
npm --prefix chat/web ci
npm --prefix chat/web run build
python -m hq.chat --data ./chat-data add-user admin --admin
python -m hq.chat --data ./chat-data add-demo
```

For local HTTP only, set `CHAT_SECURE_COOKIE=0`, then run:

```sh
python -m hq.chat --data ./chat-data serve
```

Open http://127.0.0.1:8700 and sign in with the admin password you just chose. The demo agent replies locally without API credentials. No default users, company fleet or provider credentials are shipped.

For a deployed service, retain the default `CHAT_SECURE_COOKIE=1`, place it behind an HTTPS reverse proxy and set `CHAT_PUBLIC_URL` to the public origin. The service binds to loopback by default. Keep `chat-data` private and back it up; it contains messages, uploaded files and configured provider keys. Passwords use one-way hashes; reset/generated passwords are returned only at creation/reset and cannot be retrieved later.

## Link the workspaces

Set `HQ_CHAT_URL=http://127.0.0.1:8700` before starting HQ to show its Chat workspace link. Set `CHAT_HQ_URL=http://127.0.0.1:7420` before starting Chat for the return link. Use your HTTPS origins when deployed. Sessions and permissions stay separate; no token is sent through these links.

## Add real agents

Import your local `config/fleet.json` with:

```sh
python -m hq.chat --data ./chat-data import-fleet config/fleet.json
```

Import preserves existing handles/settings. Fleet entries may supply `handle`; otherwise a handle is derived from the display name. Department agents default to Hermes and other agents to Zylos; review kinds, ownership, reports-to, visibility and configuration in Admin before use.

Zylos/Hermes agents use the outgoing bridge in [bridge/README.md](bridge/README.md). An OpenAI-compatible agent can instead be configured in Admin with its own endpoint/model/key. No external model calls are needed for the demo or the tests.

Channels support lead-agent, mention-only and all-agent modes, with bounded agent handoffs. Tasks have To do, In progress, Review and Done states. Telegram history is read-only and visible to the owner/admin under its permission rules. Admins can access private conversations; account owners should be told that when deploying to a team.

## Voice (experimental)

The Talk button uses browser speech recognition and speech synthesis; support and privacy depend on the browser/OS service. Text-only chat works independently. Voice keys are scoped to one person and one agent and stored as hashes. Create/revoke keys with `python -m hq.chat --data ./chat-data voice-key --help`. Real phone testing remains required for your environment.

## Check

```sh
python -m pip install pytest httpx
cd chat/server
python -m pytest -q tests
```

The repository CI checks this service separately from the core HQ tests and builds both frontends. Production files live in `chat/web/dist` after building; they are intentionally not committed.
