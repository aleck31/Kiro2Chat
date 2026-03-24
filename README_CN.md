# Kiro2Chat

![Version](https://img.shields.io/badge/version-0.10.0-blue)

**[English](README.md)** | **[中文](README_CN.md)**

通过 ACP 协议将 kiro-cli 桥接到各类聊天平台（Telegram、飞书等）。

## 功能

- 🔗 **ACP 协议** — 通过 JSON-RPC 2.0 over stdio 与 kiro-cli 通信
- 📱 **Telegram Bot** — 流式输出、工具调用展示、图片收发
- 🔐 **权限审批** — 敏感操作交互式 y/n/t 审批
- 🤖 **Agent / 模型切换** — `/agent` 和 `/model` 命令
- ⚡ **按需启动** — 收到消息才启动 kiro-cli，空闲自动关闭
- 🖼️ **图片支持** — 发送图片进行视觉分析（JPEG、PNG、GIF、WebP）
- 🛑 **取消** — `/cancel` 中断当前操作
- 🔧 **MCP & Skills** — 全局或工作空间级配置

## 截图

**Telegram Bot** — Agent 驱动的机器人，支持工具调用和 Markdown 渲染

<img src="docs/screenshots/kiro-tgbot-1.png" width="380"> <img src="docs/screenshots/kiro-tgbot-2.png" width="380">

**Kiro2Chat WebUI** — Gradio 多页面界面，支持模型选择和工具调用展示

<img src="docs/screenshots/kiro-webchat.png" width="780">

**MCP Config** — 启用/禁用 MCP Server，无需重启即可 Reload Agent

<img src="docs/screenshots/setting-mcp.png" width="780">

**模型配置** — 配置 Assistant Identity、Context Limit 和模型映射

<img src="docs/screenshots/setting-model.png" width="780">

## 架构

```
        ┌───────────┐ ┌─────────┐ ┌───────────┐
        │  Telegram │ │  Lark   │ │  Discord  │  ...
        │  Adapter  │ │ (todo)  │ │  (todo)   │
        └─────┬─────┘ └────┬────┘ └─────┬─────┘
              └────────────┼────────────┘
                    ┌──────┴──────┐
                    │   Bridge    │  会话管理、权限路由
                    └──────┬──────┘
                    ┌──────┴──────┐
                    │  ACPClient  │  JSON-RPC 2.0 over stdio
                    └──────┬──────┘
                    ┌──────┴──────┐
                    │  kiro-cli   │  acp 子进程
                    └─────────────┘
```

## 快速开始

```bash
# 前置条件：kiro-cli 已安装并登录
cd ~/repos/kiro2chat
uv sync
cp .env.example .env   # 设置 TG_BOT_TOKEN

kiro2chat start        # 后台启动
kiro2chat status       # 查看状态
kiro2chat stop         # 停止
```

> 运行 `kiro2chat attach` 查看实时输出（`Ctrl+B D` 退出）。

或前台运行：

```bash
uv run kiro2chat bot
```

## Telegram 命令

| 命令 | 说明 |
|------|------|
| `/model` | 查看/切换模型 |
| `/agent` | 查看/切换 Agent |
| `/cancel` | 取消当前操作 |
| `/clear` | 重置会话 |
| `/help` | 帮助 |

## 配置

### 环境变量 (`.env`)

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `TG_BOT_TOKEN` | *(必填)* | Telegram Bot Token |
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
    ├── base.py         # Adapter 接口
    └── telegram.py     # Telegram Adapter (aiogram)
```

## 相关项目

- [open-kiro](https://github.com/aleck31/open-kiro) — Kiro 的 OpenAI 兼容 API 网关

## 许可证

MIT
