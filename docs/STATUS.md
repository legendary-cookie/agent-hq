# Port status

Updated 2026-11-09.

## Done

- Fleet dashboards, accounts and server-side permissions, optional controls, refreshed GUI and first Router integration were published in the initial port.
- Multi-router discovery, tabs, per-router key actions, fallback-link handling, combined plan usage, alerts and MCP summaries. Router labels and the primary container are configured by the operator.
- Optional Chat companion with separate accounts/database: private chats, channels, tasks, agent directory/org chart, attachments, exports, live activity, Telegram history, reply/quote and experimental voice.
- Configured navigation links between HQ and Chat, a `python -m hq.chat` launcher, setup/bridge docs and separate Chat CI.
- Hash-only Chat passwords; generated/reset passwords appear once and are not recoverable. History import and activity collection are opt-in in the bridge.
- Reusable read-only agent audit script; no private fleet configuration, cloud-execution wrappers or deployment credentials included.
- Browser smoke test: local preview sign-in, agent selection and streamed demo reply succeeded; GUI and the return link rendered correctly.
- Chat: 24 backend tests pass and production frontend build/type check passes.
- HQ: 309 frontend tests pass and production build/type check passes. Full Python suite passes: 825 tests run with 49 platform skips. The 72 focused regression tests also pass.

## In progress

- None in the port implementation.

## Not done

- Shared sign-in between HQ and Chat. Each workspace currently has independent users, sessions and permissions.
- Live Proxmox/9router/bridge verification outside the original deployment. No containers or production agent instructions were changed by this port.
- Real-device voice validation; browser/OS speech support varies.
- Local routing judge rollout: the source is a single-agent pilot and has not been included in the public app.
- Dedicated backup/restore page and a generic non-Zylos cloning flow.
- Existing core frontend dependency audit findings and large build-chunk warnings remain.
