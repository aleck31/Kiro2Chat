# Deployment Guide

Complements the Quick Start in the main [README](../README.md) — this
document focuses on **platform-specific bot setup**, **production
operation**, and **data layout**.

## Platform Setup

### Telegram

1. Message [@BotFather](https://t.me/BotFather) on Telegram.
2. `/newbot` → follow prompts → get Bot Token.
3. Paste the token into the Telegram tab at `/settings`, or set
   `tg_bot_token` in `config.toml`.

### Lark / Feishu

1. Go to [Lark Open Platform](https://open.larksuite.com/app) (international)
   or [飞书开放平台](https://open.feishu.cn/app) (China).
2. Create an **Enterprise App** (企业自建应用).
3. Grab **App ID** and **App Secret** from the app credentials page.
4. Enable permissions (Tenant token scopes):
   - `im:message` — read/send direct & group messages
   - `im:message.p2p_msg:readonly` — receive DMs sent to the bot
   - `im:message.group_at_msg` — receive @bot messages in groups (if available)
   - `im:resource` — download images/files from messages
   - `contact:contact.base:readonly` — call `contact.v3.user.get` (needed to resolve display names for the authorization allowlist)
   - `contact:user.base:readonly` — read the `name` field on the user object returned by the call above
5. Subscribe to events:
   - `im.message.receive_v1` — delivery method **WebSocket** (长连接)
6. Publish the app (发布应用).
7. Configure `lark_app_id`, `lark_app_secret`, and `lark_domain` (`lark`
   for international, `feishu` for China) via `/settings` or `config.toml`.

### Discord

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications).
2. Create application → Bot → copy the Bot Token.
3. Enable **Privileged Gateway Intents** → **Message Content Intent**.
4. Bot Permissions: Send Messages, Read Message History, Attach Files.
5. Invite the bot with an OAuth2 URL (scope: `bot`, plus the permissions above).
6. Paste the token into the Discord tab at `/settings`.

## Authorization (Allowlist + Claim Tokens)

Each bot talks to `kiro-cli`, which effectively gives the caller shell access on the host. Every adapter therefore has an allowlist gated by a `Require authorization` switch in `/settings` → **Adapters**:

- **Telegram** — on by default (bot handles are public once discovered).
- **Lark / Discord** — off by default; turn on to restrict access.

With authorization on, only users on the allowlist can interact with the bot. New users join the allowlist via a one-time **claim token**:

1. Operator opens `/settings` → adapter section → click **Generate claim token** (valid 15 min).
2. User DMs the bot: `/claim <token>`.
3. Bot replies `✅ Authorized`. The user's id (and display name, if the platform exposes it) is written to `config.toml` and takes effect immediately — no restart.

The `Require authorization` toggle itself and per-user revocations from the list also apply instantly.

## Scheduled Tasks (Heartbeat)

`/settings` → **Heartbeat** lets you schedule a prompt that runs through
the ACP bridge on a fixed cadence and pushes the answer to one adapter.

Each task picks a schedule (interval like "every 2 hours", or a cron
expression), a workspace, a prompt, and a target (telegram / lark /
discord / webchat). Empty `target_chat_ids` means broadcast — to the
adapter's allowlist for bots, or to every open `/chat` tab for webchat.

If a workspace's ACP session is busy (a user is chatting), the scheduled
run queues and fires as soon as the session is free, so broadcasts are
never silently dropped.

Saving the tab restarts the scheduler; individual tasks can be fired
out-of-schedule via the ▶ button.

## Running as a systemd User Service

The README covers install (`kiro2chat install`) and day-to-day commands
(`start` / `stop` / `restart` / `status`). A few production-grade extras:

### Live logs

```bash
journalctl --user -u kiro2chat -f
```

### Keep running after logout

systemd user services stop when you log out, unless lingering is on:

```bash
sudo loginctl enable-linger $(whoami)
```

### Uninstall

```bash
kiro2chat uninstall
```

Stops the service, disables it, and removes the unit file.

## Data Directories

| Path | Purpose |
|------|---------|
| `~/.config/kiro2chat/config.toml` | Configuration (tokens, ACP params, workspaces) |
| `~/.local/share/kiro2chat/logs/` | Application logs (rotating, 20 MB × 10) |
| `~/.local/share/kiro2chat/workspaces/` | Default per-chat workspace directories |
| `~/.kiro/sessions/cli/` | ACP session files (`.json` + `.jsonl` + `.lock`) |

## Security Notes

- `config.toml` holds bot tokens in plaintext. Lock it down:
  ```bash
  chmod 600 ~/.config/kiro2chat/config.toml
  ```
- The web dashboard listens on `127.0.0.1` by default — local access only.
- To expose it externally, set `web_host = "0.0.0.0"` **and** put it behind
  a reverse proxy with TLS (nginx / Caddy / Traefik). NiceGUI does not
  ship its own TLS terminator.
- The dashboard currently has no auth. If you open it beyond localhost,
  rely on the reverse proxy for authentication (HTTP basic auth, OAuth
  proxy, or network-level ACLs).
