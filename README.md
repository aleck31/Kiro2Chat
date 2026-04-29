# Kiro2Chat


**[English](README.md)** | **[中文](docs/README_CN.md)**

Bridge kiro-cli to chat platforms (Telegram, Lark/Feishu, Discord, Web) via ACP protocol.

## Features

- 🔗 **ACP Protocol** — Communicates with kiro-cli via JSON-RPC 2.0 over stdio
- 🌐 **Web Chat** — Browser chat UI: streaming, inline permission card, message history, click-to-zoom images
- 📱 **Telegram Bot** — Streaming, tool call display, inline-keyboard permission approval, image I/O
- 💬 **Lark/Feishu Bot** — Topic-based sessions, @bot trigger, image I/O, feishu/lark domain switch
- 🎮 **Discord Bot** — @bot trigger, image I/O, 2000-char auto-split
- 🖥 **Admin Dashboard** — NiceGUI admin panel: adapter start/stop, live session stats, tabbed Settings (ACP / Workspaces / Adapters)
- 🔁 **Cross-platform session sharing** — Same workspace shares one kiro session across TG/Lark/Discord/Web
- 🔀 **Multi-workspace** — `per_chat` (each user picks via `/workspace`) or `fixed` (all chats share one)
- 🔐 **Permission Approval** — Inline keyboards (TG), inline card (Web), or text y/n/t fallback
- 🛡️ **Authorization** — Per-adapter allowlist gated by a `Require authorization` switch; new users onboard via one-time `/claim <token>`
- 🤖 **Agent & Model switching** — `/agent` and `/model` commands across all adapters
- ⚡ **On-demand startup** — kiro-cli starts when the first message arrives, idle sessions are reaped automatically
- 🖼️ **Image support** — Send images for visual analysis (JPEG, PNG, GIF, WebP); click thumbnails to preview full-size
- 🧰 **Adapter enable/disable** — Flip each bot on/off from the dashboard without touching credentials
- 🛑 **Cancel & Reset** — `/cancel` interrupts a turn, `/reset` starts a fresh session

## Screenshots

**Admin Dashboard** — adapter status, live session stats, per-adapter controls

<img src="docs/screenshots/webui-dashboard.png" width="780">

**Web Chat and Settings** — tabbed config (ACP / Workspaces / Adapters), per-tab save

<img src="docs/screenshots/webui-chatbox.png" width="380"> <img src="docs/screenshots/webui-settings-adapter.png" width="380"> <img src="docs/screenshots/webui-settings-acp.png" width="380"> <img src="docs/screenshots/webui-settings-workspace.png" width="380">

**Telegram Bot** — tool calls, inline-keyboard permission, Markdown rendering

<img src="docs/screenshots/kiro-tgbot-1.png" width="380"> <img src="docs/screenshots/kiro-tgbot-2.png" width="380">

## Architecture

```
    ┌───────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐
    │  Telegram │ │  Lark/  │ │ Discord │ │   Web   │
    │  Adapter  │ │ Feishu  │ │ Adapter │ │  Chat   │
    └─────┬─────┘ └────┬────┘ └────┬────┘ └────┬────┘
          └────────────┴──────┬────┴───────────┘
                    ┌─────────┴─────────┐
                    │      Bridge       │  session management, permission routing
                    └─────────┬─────────┘
                    ┌─────────┴─────────┐
                    │     ACPClient     │  JSON-RPC 2.0 over stdio
                    └─────────┬─────────┘
                    ┌─────────┴─────────┐
                    │     kiro-cli      │  acp subprocess
                    └───────────────────┘
```

## Quick Start

```bash
# Prerequisites: kiro-cli installed and logged in
git clone https://github.com/aleck31/Kiro2Chat.git
cd Kiro2Chat
uv sync

# Run in foreground (dev / debug)
uv run kiro2chat run

# Or deploy as a systemd user service
kiro2chat install             # generate unit file, enable service
kiro2chat start               # start daemon
kiro2chat status              # show status
kiro2chat stop                # stop daemon
```

Open `http://127.0.0.1:7860` for the admin dashboard. Configure tokens at `/settings`.

For platform-specific bot setup (BotFather, Lark/Feishu developer console,
Discord developer portal) and production operation tips, see
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Commands

All adapters support the following commands:

| Command | Description |
|---------|-------------|
| `/model` | View/switch model |
| `/agent` | View/switch agent mode |
| `/workspace` | List workspaces, marking the active one |
| `/workspace <name>` | Switch to a workspace |
| `/context` | Show context usage |
| `/cancel` | Cancel current operation |
| `/reset` | Reset session |
| `/claim <token>` | Redeem a claim token to join the allowlist |
| `/help` | Show help |

> Discord & Lark: @bot to trigger in group chats, DM for direct conversation.

## Configuration

All configuration is managed via `~/.config/kiro2chat/config.toml`, or through the Web Admin Dashboard at `/settings`.

```toml
[telegram]
tg_bot_token = "your-token"
tg_enabled = true                   # disable to keep creds but not auto-start

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
# session_id = "..."                # managed by kiro2chat, do not set manually

[workspaces.my-project]
path = "~/repos/my-project"
```

### Session sharing

Sessions are keyed by **workspace**, not chat_id. Messages sent via Telegram, Lark,
Discord, or Web that all target the same workspace land in the same kiro session —
giving cross-platform context continuity. Each prompt is tagged with `[platform/user]`
so kiro can tell messages apart.

## Project Structure

```
src/
├── app.py              # Entry point, CLI
├── config.py           # Configuration (dataclass with field factories)
├── config_manager.py   # TOML config read/write
├── log_context.py      # Logging context
├── manager.py          # Adapter lifecycle manager
├── server.py           # WebServer — hosts NiceGUI, assembles pages, boots manager
├── acp/
│   ├── client.py       # ACP JSON-RPC client (kiro-cli subprocess)
│   └── bridge.py       # Per-workspace session sharing, permission routing
├── adapters/
│   ├── base.py         # Adapter interface + shared /command dispatcher
│   ├── telegram.py     # Telegram adapter (aiogram)
│   ├── lark.py         # Lark/Feishu adapter (lark-oapi WebSocket)
│   ├── discord.py      # Discord adapter (discord.py)
│   └── web.py          # Web Chat adapter — send/receive, permission card
└── webui/
    ├── layout.py       # Shared top-nav page shell
    ├── dashboard.py    # /  — adapter status, sessions, live stats
    ├── settings.py     # /settings — tabbed config (ACP / Workspaces / Adapters)
    └── chat.py         # /chat — chat page layout + rendering helpers
```

## Tech Stack

| Component | Technology |
|-----------|------------|
| ACP Transport | JSON-RPC 2.0 over stdio |
| Web UI (Chat + Admin) | NiceGUI |
| Telegram Bot | aiogram 3 |
| Lark/Feishu Bot | lark-oapi (WebSocket) |
| Discord Bot | discord.py 2 |
| Config | TOML (config.toml) |
| Package Manager | uv + hatchling |
| Python | ≥ 3.13 |

## Related

- [open-kiro](https://github.com/aleck31/open-kiro) — OpenAI-compatible API gateway for Kiro (the API proxy counterpart)

## License

MIT
