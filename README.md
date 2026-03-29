# Kiro2Chat

![Version](https://img.shields.io/badge/version-0.13.0-blue)

**[English](README.md)** | **[中文](README_CN.md)**

Bridge kiro-cli to chat platforms (Telegram, Lark/Feishu, Discord, Web) via ACP protocol.

## Features

- 🔗 **ACP Protocol** — Communicates with kiro-cli via JSON-RPC 2.0 over stdio
- 🌐 **Web Chat** — NiceGUI-based chat UI with streaming output
- 📱 **Telegram Bot** — Full-featured bot with streaming, tool call display, image I/O
- 💬 **Lark/Feishu Bot** — Topic-based sessions, @bot trigger, image I/O, feishu/lark domain switch
- 🎮 **Discord Bot** — @bot trigger, image I/O, 2000-char auto-split
- 🔐 **Permission Approval** — Interactive y/n/t approval for sensitive operations
- 🤖 **Agent & Model Switching** — `/agent` and `/model` commands across all adapters
- ⚡ **On-Demand Startup** — kiro-cli starts when first message arrives, auto-stops on idle
- 🖼️ **Image Support** — Send images for visual analysis (JPEG, PNG, GIF, WebP)
- 🛑 **Cancel** — `/cancel` to interrupt current operation
- 🔧 **MCP & Skills** — Global or workspace-level config via `.kiro/`

## Screenshots

**Telegram Bot** — Agent-powered bot with tool calling and Markdown rendering

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
cd ~/repos/kiro2chat
uv sync
cp .env.example .env   # set TG_BOT_TOKEN / LARK_APP_ID+SECRET / DISCORD_BOT_TOKEN

kiro2chat start telegram   # start Telegram bot in background
kiro2chat start lark       # start Lark/Feishu bot in background
kiro2chat start discord    # start Discord bot in background
kiro2chat start web        # start Web Chat UI in background
kiro2chat status           # check status
kiro2chat stop telegram    # stop
```

> Run `kiro2chat attach telegram` to view live output (detach with `Ctrl+B D`).

Or run directly in foreground:

```bash
uv run kiro2chat telegram
uv run kiro2chat lark
uv run kiro2chat discord
uv run kiro2chat web
```

## Commands

All adapters support the following commands:

| Command | Description |
|---------|-------------|
| `/model` | View/switch model |
| `/agent` | View/switch agent mode |
| `/cancel` | Cancel current operation |
| `/clear` | Reset session |
| `/help` | Show help |

> Discord & Lark: @bot to trigger in group chats, DM for direct conversation.

## Configuration

### Environment Variables (`.env`)

| Variable | Default | Description |
|----------|---------|-------------|
| `TG_BOT_TOKEN` | *(required for Telegram)* | Telegram Bot token |
| `LARK_APP_ID` | *(required for Lark)* | Lark/Feishu App ID |
| `LARK_APP_SECRET` | *(required for Lark)* | Lark/Feishu App Secret |
| `LARK_DOMAIN` | `feishu` | `feishu` (国内) or `lark` (international) |
| `DISCORD_BOT_TOKEN` | *(required for Discord)* | Discord Bot token |
| `WEB_HOST` | `127.0.0.1` | Web Chat listen host |
| `WEB_PORT` | `8080` | Web Chat listen port |
| `KIRO_CLI_PATH` | `kiro-cli` | Path to kiro-cli binary |
| `WORKSPACE_MODE` | `per_chat` | `per_chat` (isolated) or `fixed` (shared dir) |
| `WORKING_DIR` | `~/.local/share/kiro2chat/workspaces` | Workspace root |
| `IDLE_TIMEOUT` | `300` | Seconds before idle kiro-cli stops (0=disable) |
| `LOG_LEVEL` | `info` | Log level |

### Config File (`config.toml`)

`~/.config/kiro2chat/config.toml` — same variables as above, env vars take priority.

### MCP & Skills

- Global: `~/.kiro/settings/mcp.json`, `~/.kiro/skills/`
- Workspace: `{WORKING_DIR}/.kiro/settings/mcp.json` (fixed mode only)

## Project Structure

```
src/
├── app.py              # Entry point, CLI, tmux management
├── config.py           # Configuration
├── config_manager.py   # TOML config read/write
├── log_context.py      # Logging context
├── acp/
│   ├── client.py       # ACP JSON-RPC client (kiro-cli subprocess)
│   └── bridge.py       # Session management, event routing
└── adapters/
    ├── base.py         # Adapter interface
    ├── telegram.py     # Telegram adapter (aiogram)
    ├── lark.py         # Lark/Feishu adapter (lark-oapi SDK)
    ├── discord.py      # Discord adapter (discord.py)
    └── web.py          # Web Chat adapter (NiceGUI)
```

## Tech Stack

| Component | Technology |
|-----------|------------|
| ACP Transport | JSON-RPC 2.0 over stdio |
| Web Chat | NiceGUI |
| Telegram Bot | aiogram 3 |
| Lark/Feishu Bot | lark-oapi (WebSocket) |
| Discord Bot | discord.py 2 |
| Config | python-dotenv + TOML |
| Package Manager | uv + hatchling |
| Python | ≥ 3.13 |

## Related

- [open-kiro](https://github.com/aleck31/open-kiro) — OpenAI-compatible API gateway for Kiro (the API proxy counterpart)

## License

MIT
