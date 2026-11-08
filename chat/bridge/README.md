# Outgoing agent bridge

`agent_hq_chat_bridge.py` runs as the agent login and uses Python 3.9+ standard library only. It long-polls Chat with an agent-specific token. It does not give Chat an SSH login.

Generate a config once (this rotates that agent’s bridge token):

```sh
python -m hq.chat --data ./chat-data bridge-config ct301-assistant --server https://chat.example.com --ca-file /etc/chat-ca.crt
```

Save the JSON privately on the agent (mode 0600). Set `HQ_CHAT_CONFIG` to its absolute path. The default is `/etc/agent-hq-chat-bridge.json`. Use a trusted CA, then run `python3 agent_hq_chat_bridge.py check` and `python3 agent_hq_chat_bridge.py run` as the agent login. For public certificates the config can omit `ca_file`.

For Zylos, install `zylos-webchat/` into that agent’s channel/skill directory. Set `HQ_CHAT_BRIDGE` to this bridge script’s absolute path if it is not installed at `/opt/agent-hq-chat-bridge/agent_hq_chat_bridge.py`. The `zylos.dir` config field identifies the active Zylos directory. Agent instructions must allow named Chat users and replies through the webchat channel. No original company trust rules are installed automatically.

Hermes can use its CLI with per-conversation resume, or its API mode configured in the `hermes` section. Check `agent_hq_chat_bridge.py` for supported settings before changing an existing agent.

## Optional history and activity

Both are disabled by default in this public port. To enable explicitly, merge these fields into the config:

```json
{"telegram": {"sync": true}, "activity": {"enabled": true}}
```

Telegram sync reads Zylos’s local `comm-bridge/c4.db` and sends message text to Chat. Activity reads the agent’s session logs and can include inputs, outputs, tools and logged reasoning. Chat applies owner/admin/activity permissions. Enable only for agents whose logs you intend to collect. The `tg-sync` command also runs an explicit one-off import; `--dry-run` reports without uploading.

Bridge tokens should be kept private. Regenerate an agent token in Admin or the CLI to revoke an old bridge. Server URLs, CA files, user names and system-service setup must be supplied for your own environment.
