# Deployment Guide

## Prerequisites

- Python ≥ 3.13
- [uv](https://docs.astral.sh/uv/) package manager
- kiro-cli installed and logged in (`kiro-cli login`)

## Quick Start

```bash
git clone https://github.com/aleck31/Kiro2Chat.git
cd Kiro2Chat
uv sync

# Run directly
uv run kiro2chat start
```

Open `http://127.0.0.1:7860` for the admin dashboard. Configure tokens in `/settings` page.

## Configuration

All config lives in `~/.config/kiro2chat/config.toml`. No `.env` file needed.

```toml
[telegram]
tg_bot_token = "your-token"
tg_enabled = true

[lark]
lark_app_id = "cli_xxx"
lark_app_secret = "xxx"
lark_domain = "feishu"              # feishu | lark
lark_enabled = true

[discord]
discord_bot_token = "your-token"
discord_enabled = true

[web]
web_host = "127.0.0.1"
web_port = 7860

[acp]
kiro_cli_path = "kiro-cli"
workspace_mode = "per_chat"         # per_chat | fixed
fixed_workspace = "default"         # only used when workspace_mode = "fixed"
idle_timeout = 1200                 # seconds before idle session reap; 0 disables
response_timeout = 3600             # max wait per prompt

[workspaces.default]
path = "~/.local/share/kiro2chat/workspaces/default"
```

You can also edit config via the web dashboard at `/settings`.

## systemd Service (Recommended)

### 1. Install

```bash
deploy/install.sh
```

The script auto-detects project directory and `uv` path, generates the service file from template, and enables it.

### 2. Manage

```bash
kiro2chat start               # start daemon
kiro2chat stop                # stop daemon
kiro2chat restart             # restart daemon
kiro2chat status              # show status

# Or use systemctl directly
journalctl --user -u kiro2chat -f   # live logs
```

### 3. Enable lingering (keep running after logout)

```bash
sudo loginctl enable-linger $(whoami)
```

## CLI Commands

```bash
kiro2chat start               # start daemon (web dashboard + configured adapters)
kiro2chat stop                # stop daemon
kiro2chat restart             # restart daemon
kiro2chat status              # show daemon status

# Start individual adapter in foreground
kiro2chat adapter telegram
kiro2chat adapter lark
kiro2chat adapter discord
```

## Platform Setup

### Telegram

1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. `/newbot` → follow prompts → get Bot Token
3. Set `tg_bot_token` in config.toml or `/settings` page

### Lark / Feishu

1. Go to [Lark Open Platform](https://open.larksuite.com/app) (international) or [飞书开放平台](https://open.feishu.cn/app) (China)
2. Create an **Enterprise App** (企业自建应用)
3. Get **App ID** and **App Secret** from app credentials page
4. Enable permissions (Tenant token scopes):
   - `im:message` — Read and send direct messages and group chat messages
   - `im:message.p2p_msg:readonly` — Get direct messages sent to bot
   - `im:message.group_at_msg` — Receive @bot messages in groups (if available)
   - `im:resource` — Download images/files from messages
5. Subscribe to events:
   - `im.message.receive_v1` — Receive messages
   - Delivery method: **WebSocket** (长连接)
6. Publish the app (发布应用)
7. Set `lark_app_id`, `lark_app_secret`, `lark_domain` in config.toml
   - `lark_domain = "lark"` for international, `"feishu"` for China

### Discord

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Create application → Bot → get Bot Token
3. Enable **Privileged Gateway Intents**:
   - Message Content Intent ✅
4. Bot Permissions: Send Messages, Read Message History, Attach Files
5. Invite bot to server with OAuth2 URL (scope: `bot`, permissions above)
6. Set `discord_bot_token` in config.toml

## Data Directories

| Path | Purpose |
|------|---------|
| `~/.config/kiro2chat/config.toml` | Configuration (tokens, ACP params, workspaces) |
| `~/.local/share/kiro2chat/logs/` | Application logs (rotating, 20MB × 10) |
| `~/.local/share/kiro2chat/workspaces/` | Per-chat workspace directories |
| `~/.kiro/sessions/cli/` | ACP session files (JSON + JSONL + lock) |

## Security Notes

- Tokens stored in `config.toml` — ensure file permissions: `chmod 600 ~/.config/kiro2chat/config.toml`
- Web dashboard listens on `127.0.0.1` by default (local only)
- To expose externally, set `web_host = "0.0.0.0"` and use a reverse proxy with TLS
