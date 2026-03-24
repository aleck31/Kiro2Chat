# Kiro2Chat

![Version](https://img.shields.io/badge/version-0.10.0-blue)

**[English](README.md)** | **[中文](README_CN.md)**

Bridge kiro-cli to chat platforms (Telegram, Lark, etc.) via ACP protocol.

## Features

- 🔗 **ACP Protocol** — Communicates with kiro-cli via JSON-RPC 2.0 over stdio
- 📱 **Telegram Bot** — Full-featured bot with streaming, tool call display, image I/O
- 🔐 **Permission Approval** — Interactive y/n/t approval for sensitive operations
- 🤖 **Agent & Model Switching** — `/agent` and `/model` commands
- ⚡ **On-Demand Startup** — kiro-cli starts when first message arrives, auto-stops on idle
- 🖼️ **Image Support** — Send images for visual analysis (JPEG, PNG, GIF, WebP)
- 🛑 **Cancel** — `/cancel` to interrupt current operation
- 🔧 **MCP & Skills** — Global or workspace-level config via `.kiro/`

## Screenshots

**Telegram Bot** — Agent-powered bot with tool calling and Markdown rendering

<img src="docs/screenshots/kiro-tgbot-1.png" width="380"> <img src="docs/screenshots/kiro-tgbot-2.png" width="380">

**Kiro2Chat WebUI** — Gradio multi-page UI with model selector and tool call display

<img src="docs/screenshots/kiro-webchat.png" width="780">

**MCP Config** — Enable/disable MCP servers and reload agent without restart

<img src="docs/screenshots/setting-mcp.png" width="780">

**Model Config** — Configure assistant identity, context limit, and model mapping

<img src="docs/screenshots/setting-model.png" width="780">

## Architecture

```
        ┌───────────┐ ┌─────────┐ ┌───────────┐
        │  Telegram │ │  Lark   │ │  Discord  │  ...
        │  Adapter  │ │ (todo)  │ │  (todo)   │
        └─────┬─────┘ └────┬────┘ └─────┬─────┘
              └────────────┼────────────┘
                    ┌──────┴──────┐
                    │   Bridge    │  session management, permission routing
                    └──────┬──────┘
                    ┌──────┴──────┐
                    │  ACPClient  │  JSON-RPC 2.0 over stdio
                    └──────┬──────┘
                    ┌──────┴──────┐
                    │  kiro-cli   │  acp subprocess
                    └─────────────┘
```

## Quick Start

```bash
# Prerequisites: kiro-cli installed and logged in
cd ~/repos/kiro2chat
uv sync
cp .env.example .env   # set TG_BOT_TOKEN

kiro2chat start        # start bot in background
kiro2chat status       # check status
kiro2chat stop         # stop
```

> Run `kiro2chat attach` to view live output (detach with `Ctrl+B D`).

Or run directly in foreground:

```bash
uv run kiro2chat bot
```

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/model` | View/switch model |
| `/agent` | View/switch agent mode |
| `/cancel` | Cancel current operation |
| `/clear` | Reset session |
| `/help` | Show help |

## Configuration

### Environment Variables (`.env`)

| Variable | Default | Description |
|----------|---------|-------------|
| `TG_BOT_TOKEN` | *(required)* | Telegram Bot token |
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
    └── telegram.py     # Telegram adapter (aiogram)
```

## Tech Stack

| Component | Technology |
|-----------|------------|
| ACP Transport | JSON-RPC 2.0 over stdio |
| Telegram Bot | aiogram 3 |
| Config | python-dotenv + TOML |
| Package Manager | uv + hatchling |
| Python | ≥ 3.13 |

## Related

- [open-kiro](https://github.com/aleck31/open-kiro) — OpenAI-compatible API gateway for Kiro (the API proxy counterpart)

## License

MIT
