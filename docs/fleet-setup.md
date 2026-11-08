# Fleet setup

Agent HQ works locally without these integrations. No company configuration or credentials are bundled.

## Accounts

Run `python -m hq --set-password` to set the initial admin password. For an HTTPS reverse proxy, use `--public-url https://hq.example.com`. Sign in as `admin`, then use Accounts to create users and assign Admin, Operator, Viewer or custom roles. Page visibility and action permissions are enforced by the server. Password or role changes invalidate that user’s sessions. Sensitive actions require a password re-check.

## Proxmox (optional)

Set `HQ_PVE_HOST` to an SSH target such as `root@pve.example.com` before starting HQ. With no target, Proxmox controls stay unconfigured. Place a dedicated SSH private key at `.ssh/pve_ed25519` and verified host keys at `.ssh/pve_known_hosts`, beside `hq.db`. Host checking is strict. HQ needs permission to run `pct` and `pvesh`; its control integration assumes a Proxmox Linux host and Linux LXC agents.

Watched agents in `config/hosts.json` need names like `ct301-assistant` and their own SSH login, such as `agentuser@agent.example.com`. Only watched containers can be controlled. Container 500 remains reserved and excluded. The UI offers power actions, session messages, terminal access, updates, memory and settings; these require the corresponding account permissions.

## New-agent templates

Creation clones an existing Zylos container using the `/opt/zylos` layout, systemd services and a Telegram bot. It is not a generic installer for every harness. Review the template and its retained company knowledge before cloning.

Set these environment variables for your network:

- `HQ_AGENT_SUBNET`: IPv4 subnet (default `192.0.2.0/24`, a documentation-only network).
- `HQ_AGENT_GATEWAY`: gateway (default `192.0.2.1`).
- `HQ_AGENT_STORAGE`: Proxmox clone storage (default `local-lvm`).

Suggested addresses use offsets 60–99 within the subnet. Configure a subnet large enough for that range. Each new container boots with networking disconnected while copied identities, credentials and conversation history are removed. The UI reports any partial failure so the container can be inspected.

## 9router (optional)

The Router page discovers a Proxmox container with `9router` in its hostname. Every matching router is monitored. The Router page has a tab for each, and key actions target the selected router. It expects 9router on port 20128, its database at `/var/lib/9router/db/data.sqlite`, and optional rotation/model-catalog files in `/var/lib/9router`. Schema changes in 9router may require an HQ update.

Health and usage reads use allowlisted fields. Key management calls the router’s localhost API inside its container. Key values leave the router only for explicit create/copy actions; keep Router permissions restricted. Plan percentages estimate usage attribution from observed traffic and provider limits; dollar values use the configured price book.

## MCP access

Use Agent keys to create a scoped token and copy the generated MCP connection command. Keep tokens private. This uses the same running HQ server and logs actions in Activity.


### Multiple router labels

`HQ_ROUTER_PRIMARY` selects the container ID used for the backward-compatible top-level summary. Otherwise the first discovered/sorted router is used. `HQ_ROUTER_LABELS` is a JSON object mapping container IDs to `[label, description]`, for example `{"301": ["Primary", "Team agents"], "302": ["Fallback", "Overflow"]}`. With no custom labels, router tabs use their container IDs. Fallback provider links are shown separately and excluded from plan-account totals.

### Optional Chat workspace

See [Chat setup](../chat/README.md). Set `HQ_CHAT_URL` to its HTTP/HTTPS origin to show the navigation link. Chat runs independently with separate users and permissions.

### Read-only agent audit

`tools/agent-audit.py` inventories local agent settings, model routing and recent usage when run inside a Linux agent container. It masks common secret-looking fields. Review the report before sharing it: it can still contain operational paths and metadata. No cloud account or container-execution wrapper is bundled.
