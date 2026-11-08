#!/usr/bin/env node
// Zylos channel "webchat": c4-send webchat <endpoint> "<message>" ends up here.
// Hands the message to the Agent HQ Chat bridge, which posts it as this agent.
const { spawnSync } = require('child_process');
const [endpoint, ...rest] = process.argv.slice(2);
if (!endpoint || !rest.length) { console.error('usage: send.js <endpoint> <message>'); process.exit(2); }
const bridge = process.env.HQ_CHAT_BRIDGE || '/opt/agent-hq-chat-bridge/agent_hq_chat_bridge.py';
const r = spawnSync('python3', [bridge, 'send', endpoint, '-'], { input: rest.join(' '), encoding: 'utf8', timeout: 60000 });
if (r.status !== 0) { console.error((r.stderr || r.stdout || 'send failed').trim()); process.exit(1); }
console.log((r.stdout || 'sent').trim());
