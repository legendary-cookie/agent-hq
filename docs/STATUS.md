# Port status

Updated 2026-11-08.

## Done

- Port fleet Overview, Agents, Usage, Insights, Trends and office views.
- Port accounts and server-side permissions, optional agent controls, Router integration, MCP tools and reliability fixes.
- Replace company identifiers and network addresses with generic examples; no deployment credentials or private configuration included.
- Require an explicit Proxmox SSH target and document network/storage configuration and integration limits.
- Stop monitoring workers when the HTTP server closes; make the limits probe self-contained.
- Web: 55 test files, 307 tests passed; TypeScript and production build passed.
- Python: 819 tests executed, 49 platform skips. One test encountered an unavailable WSL network path during setup and passed when rerun alone; all other tests passed.

## In progress

- None in this port.

## Not done

- Live Proxmox/9router verification outside the original deployment.
- Multi-router support and generic non-Zylos agent cloning.
- Existing frontend dependency audit findings and large build-chunk warnings remain.
