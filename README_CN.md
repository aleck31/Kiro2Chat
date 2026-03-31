# Kiro2Chat

![Version](https://img.shields.io/badge/version-0.13.0-blue)

**[English](README.md)** | **[中文](README_CN.md)**

通过 ACP 协议将 kiro-cli 桥接到各类聊天平台（Telegram、飞书、Discord、Web）。

## 功能

- 🔗 **ACP 协议** — 通过 JSON-RPC 2.0 over stdio 与 kiro-cli 通信
- 🌐 **Web Chat** — 基于 NiceGUI 的聊天界面，流式输出、图片上传
- 📱 **Telegram Bot** — 流式输出、工具调用展示、图片收发
- 💬 **飞书 Bot** — 话题 session 映射、@bot 触发、图片收发、飞书/Lark 域名切换
- 🎮 **Discord Bot** — @bot 触发、图片收发、2000 字符自动分段
- 🔐 **权限审批** — 敏感操作交互式 y/n/t 审批
- 🤖 **Agent / 模型切换** — 所有 adapter 均支持 `/agent` 和 `/model` 命令
- ⚡ **按需启动** — 收到消息才启动 kiro-cli，空闲自动关闭
- 🖼️ **图片支持** — 发送图片进行视觉分析（JPEG、PNG、GIF、WebP）
- 🛑 **取消** — `/cancel` 中断当前操作
- 🔧 **MCP & Skills** — 全局或工作空间级配置

## 截图

**Telegram Bot** — Agent 驱动的机器人，支持工具调用和 Markdown 渲染

<img src="docs/screenshots/kiro-tgbot-1.png" width="380"> <img src="docs/screenshots/kiro-tgbot-2.png" width="380">

## 架构

```
    ┌───────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐
    │  Telegram │ │  Lark/  │ │ Discord │ │   Web   │
    │  Adapter  │ │ Feishu  │ │ Adapter │ │  Chat   │
    └─────┬─────┘ └────┬────┘ └────┬────┘ └────┬────┘
          └────────────┴──────┬────┴───────────┘
                    ┌─────────┴─────────┐
                    │      Bridge       │  会话管理、权限路由
                    └─────────┬─────────┘
                    ┌─────────┴─────────┐
                    │     ACPClient     │  JSON-RPC 2.0 over stdio
                    └─────────┬─────────┘
                    ┌─────────┴─────────┐
                    │     kiro-cli      │  acp 子进程
                    └───────────────────┘
```

## 快速开始

```bash
# 前置条件：kiro-cli 已安装并登录
cd ~/repos/kiro2chat
uv sync
cp .env.example .env   # 设置 TG_BOT_TOKEN / LARK_APP_ID+SECRET / DISCORD_BOT_TOKEN

kiro2chat start telegram   # 后台启动 Telegram
kiro2chat start lark       # 后台启动飞书
kiro2chat start discord    # 后台启动 Discord
kiro2chat start web        # 后台启动 Web Chat
kiro2chat status           # 查看状态
kiro2chat stop telegram    # 停止
```

> 运行 `kiro2chat attach telegram` 查看实时输出（`Ctrl+B D` 退出）。

或前台运行：

```bash
uv run kiro2chat telegram
uv run kiro2chat lark
uv run kiro2chat discord
uv run kiro2chat web
```

## 命令

所有 adapter 均支持以下命令：

| 命令 | 说明 |
|------|------|
| `/model` | 查看/切换模型 |
| `/agent` | 查看/切换 Agent |
| `/cancel` | 取消当前操作 |
| `/clear` | 重置会话 |
| `/help` | 帮助 |

> Discord 和飞书：群聊中 @bot 触发，私聊直接对话。

## 配置

### 环境变量 (`.env`)

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `TG_BOT_TOKEN` | *(Telegram 必填)* | Telegram Bot Token |
| `LARK_APP_ID` | *(飞书必填)* | 飞书 App ID |
| `LARK_APP_SECRET` | *(飞书必填)* | 飞书 App Secret |
| `LARK_DOMAIN` | `feishu` | `feishu`（国内）或 `lark`（国际版）|
| `DISCORD_BOT_TOKEN` | *(Discord 必填)* | Discord Bot Token |
| `WEB_HOST` | `127.0.0.1` | Web Chat 监听地址 |
| `WEB_PORT` | `7860` | Web Chat 监听端口 |
| `KIRO_CLI_PATH` | `kiro-cli` | kiro-cli 路径 |
| `WORKSPACE_MODE` | `per_chat` | `per_chat`（隔离）或 `fixed`（共享目录）|
| `WORKING_DIR` | `~/.local/share/kiro2chat/workspaces` | 工作空间根目录 |
| `IDLE_TIMEOUT` | `300` | 空闲超时秒数（0=禁用）|
| `LOG_LEVEL` | `info` | 日志级别 |

### 配置文件 (`config.toml`)

`~/.config/kiro2chat/config.toml` — 同上，环境变量优先。

### MCP & Skills

- 全局：`~/.kiro/settings/mcp.json`、`~/.kiro/skills/`
- 工作空间：`{WORKING_DIR}/.kiro/settings/mcp.json`（仅 fixed 模式）

## 项目结构

```
src/
├── app.py              # 入口、CLI、tmux 管理
├── config.py           # 配置
├── config_manager.py   # TOML 配置读写
├── log_context.py      # 日志上下文
├── acp/
│   ├── client.py       # ACP JSON-RPC 客户端
│   └── bridge.py       # 会话管理、事件路由
└── adapters/
    ├── base.py         # Adapter 基类
    ├── telegram.py     # Telegram Adapter (aiogram)
    ├── lark.py         # 飞书 Adapter (lark-oapi SDK)
    ├── discord.py      # Discord Adapter (discord.py)
    └── web.py          # Web Chat Adapter (NiceGUI)
```

## 技术栈

| 组件 | 技术 |
|------|------|
| ACP 传输 | JSON-RPC 2.0 over stdio |
| Web Chat | NiceGUI |
| Telegram Bot | aiogram 3 |
| 飞书 Bot | lark-oapi (WebSocket) |
| Discord Bot | discord.py 2 |
| 配置 | python-dotenv + TOML |
| 包管理 | uv + hatchling |
| Python | ≥ 3.13 |

## 相关项目

- [open-kiro](https://github.com/aleck31/open-kiro) — Kiro 的 OpenAI 兼容 API 网关

## 许可证

MIT
