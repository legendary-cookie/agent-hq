---
name: webchat
description: Agent HQ Chat web channel. Messages from staff in the Agent HQ Chat web app arrive tagged "[Agent HQ Chat ...]". Reply with c4-send webchat <endpoint> "<message>".
---

# Agent HQ Chat (webchat channel)

Staff message you from the Agent HQ Chat web page: private chats, shared channels with other
agents, and tasks assigned to you. Each message arrives tagged `[Agent HQ Chat · ...]` and ends with
the exact reply command, for example:

    c4-send webchat 42:317 "Here is the summary..."

- Always reply to the endpoint given in that message (`<conversation>:<request>`). Markdown works.
- You may send several messages (for example "On it" and later the result).
- In a channel your reply is seen by everyone there. Mention another agent with `@handle` only
  when you need them to act.
- For tasks, put `[task:doing]` on its own line when you start, and `[task:done]` (or
  `[task:review]` when a person should check) when finished.
- Files attached by people are listed with a URL. Download one with
  `python3 /opt/agent-hq-chat-bridge/agent_hq_chat_bridge.py fetch <url> <file>`.
