# Changelog

### v0.15.0 — Multi-Workspace Switching
- **Workspace 管理** — `config.toml` 新增 `[workspaces]` 节（name → path 映射）
- **Bridge 路由重构** — session 路由从 `chat_id` 改为 `(chat_id, workspace_name)` 二元组
  - `switch_workspace()` / `get_active_workspace()` / `get_workspaces()` API
  - `clear()` 方法替代直接操作 `_sessions`
  - 切换 workspace 只改指针，旧 session 等 idle timeout 自然回收
- **`/workspace` 命令** — 四个 adapter 统一支持
  - `/workspace` — 显示当前 workspace
  - `/workspace list` — 列出所有，标记当前（✓）
  - `/workspace switch <name>` — 切换
- **Admin Config 页** — Workspace 管理 UI（列表/新增/删除 name→path）
- **`handle_workspace_command()`** — `base.py` 共享命令逻辑，避免四个 adapter 重复实现
- 73 个测试（新增 Bridge workspace 4 + Web workspace 5）

### v0.14.0 — Web Admin Dashboard
- **Web Admin Dashboard** — `/` 管理首页，adapter 状态卡片（running/stopped/unconfigured）、start/stop 控制、活跃 session 表
- **Config 页面** — `/config` 配置 Token、ACP 参数，保存到 `config.toml`
- **Web Chat 移至 `/chat`** — 原 `/` 聊天界面移至 `/chat`
- **AdapterManager** — `src/manager.py`，进程内 adapter 生命周期管理（start/stop/状态查询）
- **移除 dotenv 依赖** — 配置统一由 `config.toml` 管理，删除 `.env.example`
- **config.py 重构** — 移除 `load_dotenv()`，新增 `reload()` 函数
- **config_manager.py** — `_SECTIONS` 映射更新，覆盖所有配置项
- 66 个测试（新增 Manager 5 + Web mask 3）

### v0.13.0 — Discord Adapter
- **Discord Adapter** — `src/adapters/discord.py`，基于 discord.py，Message Content Intent
  - @bot 触发（guild）/ 私聊直接响应
  - 图片接收、流式输出（message.edit）、2000 字符自动分段
  - `/model` `/agent` `/cancel` `/clear` `/help` 命令
  - 权限审批（文本回复 y/n/t）、并发消息锁
- **Web Chat 图片上传** — `ui.upload` 组件，选图后随消息一起发送
- **BaseAdapter 简化** — 移除过时的 abstract methods
- **配置** — 新增 `DISCORD_BOT_TOKEN`
- 56 个测试（新增 Discord 10 + Web 12）

### v0.12.0 — Web Chat, 命令对齐
- **Web Chat Adapter** — `src/adapters/web.py`，基于 NiceGUI，流式输出、权限审批、命令支持
- **Lark 命令补齐** — `/model`、`/agent`、`/cancel`、`/clear` 命令支持
- **命令统一** — 三个 adapter 均支持 `/model`、`/agent`、`/cancel`、`/clear`，仅匹配 `/` 开头
- **Lark 并发锁修复** — `bridge.prompt` 调用移入 `with lock` 块内
- **CLI** — 新增 `web` service，`kiro2chat start web`
- **配置** — 新增 `WEB_HOST`（默认 127.0.0.1）、`WEB_PORT`（默认 8080）

### v0.11.0 — Lark/Feishu Adapter
- **Lark/Feishu Adapter** — `src/adapters/lark.py`，基于 lark-oapi SDK WebSocket 长连接
  - 话题（topic）映射 ACP session，群聊共享 session
  - @bot 触发，私聊直接响应
  - 流式更新、权限审批（文本回复 y/n/t）
  - 图片接收（下载 → base64 → ACP）和发送
  - 并发消息锁 + "上一条还在处理中"提示
  - `LARK_DOMAIN` 配置：`feishu`（国内）/ `lark`（国际版）
- **CLI 命名规范** — `bot` → `telegram`，`kiro2chat start telegram|lark`
- **Telegram Adapter 测试** — 补充 13 个单元测试
- **Lark Adapter 测试** — 8 个单元测试（chat_id、is_mentioned、extract_text）
- 34 个测试全部通过

### v0.10.0 — ACP 架构重构
- **架构重构** — 从 API 网关转型为 ACP chat bridge，通过 `kiro-cli acp` 子进程通信
- **ACP Client** — `src/acp/client.py`，JSON-RPC 2.0 over stdio，支持流式输出、工具调用、权限审批、图片输入、模式/模型切换
- **Bridge** — `src/acp/bridge.py`，会话管理（per-chat 隔离）、按需启动 kiro-cli、空闲超时自动关闭
- **Adapter 抽象** — `src/adapters/base.py` 定义平台 adapter 接口
- **Telegram Adapter** — 重写为通过 Bridge 调用，新增 `/agent` 命令、权限审批 inline keyboard、`/cancel` 对接 `session/cancel`
- **群聊共享 session** — 同群用户共享 session 和 workspace，私聊独立
- **移除 API 网关代码** — 删除 FastAPI、Strands Agent、OpenAI/Anthropic 兼容路由、WebUI、Prometheus 等（已迁移至 open-kiro 项目）
- **依赖精简** — 从 12 个依赖精简至 3 个（python-dotenv, aiogram, tomli-w）
- 13 个测试（ACP Client 8 + Bridge 5）

### v0.9.5
- **CLI tmux 管理** — `kiro2chat <action> [service]` 支持后台 start/stop/restart/status/attach，per-component tmux session（`kiro2chat-api/webui/bot`），退役 `kiro2chat.sh`
- **assistant_identity 配置** — `config.toml` + Web UI 下拉（kiro/claude），控制 anti-prompt 注入和响应清洗强度
- **context_limit 配置** — `config.toml` + Web UI 数字输入，请求前预检 token 数（默认 190000）
- **并行工具调用 400 修复** — `converter.py` 正确收集所有尾部 toolResult 消息，避免 history/current 分裂导致 400
- **per-session Agent 隔离** — `agent_routes.py` 按 `x-user-tag`/`session_id` 维护独立会话，TTL 12h 自动清理
- **滑动窗口** — Agent 使用 `SlidingWindowConversationManager(window_size=20)`
- **Favicon** — Web UI 使用 `docs/favicon.png`
- 新增 2 个测试（共 30 个）

### v0.9.4
- **assistant_identity** — `config.toml` + Web UI 下拉（kiro/claude），控制 anti-prompt 注入和响应清洗强度（v0.9.5 合并前的独立版本）

### v0.9.3
- **MCP enable/disable** — `config.toml` 控制启用/禁用 MCP server，Web UI Settings 页可切换并热重载
- **HTTP/SSE MCP 支持** — `agent.py` 支持 `http`/`sse` transport 类型
- **Monitor 页增强** — 实时日志流、MCP server 状态展示
- **Settings 页重构** — MCP 配置与模型配置分离为独立 tab

### v0.9.1
- `data_dir` 配置项（默认 `~/.local/share/kiro2chat`），统一日志、数据文件路径
- `kiro2chat@.service` systemd 模板更新
- Agent system prompt 精简

### v0.9.0
- **Anthropic Messages API** — `/v1/messages` 完整兼容（流式 + 非流式），支持 tool_use/tool_result、image blocks、thinking blocks、auto-continuation
- `/v1/messages/count_tokens` token 估算端点
- **三层提示词防御** — `sanitizer.py` anti-prompt 注入 + assistant 确认 + 响应清洗（identity/tool scrub）
- `converter.py` 集成 `build_system_prompt()`，始终注入 anti-prompt（不再依赖用户是否提供 system prompt）
- `routes.py` 流式和非流式输出集成 `sanitize_text()` 响应清洗
- **Token 计数** — `token_counter.py` tiktoken cl100k_base 优先，CJK 感知 fallback
- **Prometheus 监控** — `metrics.py` + `/metrics` 端点（请求计数、延迟直方图、token 统计、工具调用、错误、重试）
- **健康检查** — `health.py` + `/health` 端点
- **Client 重试** — 5xx + timeout 自动重试，指数退避 1s→3s→10s，最多 3 次
- Client 超时 120s → 7200s（2 小时，适配长输出）
- Client 添加 `KiroIDE` User-Agent header
- CORS middleware
- GitHub Actions CI（pytest + ruff）
- systemd 服务模板 `kiro2chat@.service`
- 28 个测试（sanitizer 15 + token_counter 7 + converter 6）
- Agent system prompt 更新：身份改为 kiro2chat，去掉过度强制的工具使用指令
- README 改为英文，新增 README_CN.md、CONTRIBUTING.md、docs/DEPLOYMENT.md
- 感谢 @neosun100 (PR #1) 贡献核心功能代码

### v0.8.1
- WebUI 拆分为包 `src/webui/`（chat, monitor, settings）
- Chat 页：多模态输入（图片上传）、Agent 图片输出显示
- Chat 页：Chatbot 高度 60vh、可折叠工具列表、隐藏 share 按钮和 footer
- 日志系统：双 handler（console + rotating file 20MB×10）、ContextVar 用户标签
- 配置拆分：`.env`（secrets + 启动参数）vs `config.toml`（模型配置，Web UI 可编辑）
- 提取 CHANGELOG.md，创建 .env.example

### v0.8.0
- Agent API 多模态支持：`/v1/agent/chat` 新增 `images` 参数，构造 Strands ContentBlock 列表
- Converter 图片支持：`_extract_images()` 处理 OpenAI `image_url` 和 Bedrock `image` 格式，转为 Kiro images 格式
- TG Bot 图片输入：支持 photo 和 document（大图/PNG）两种方式发送图片给 Agent
- 流式中间状态 HTML 渲染：`tool_start` 和每 N chunk 更新也应用 `_md_to_html` + `parse_mode=HTML`

### v0.7.0
- TG Bot Markdown 渲染：`_md_to_html()` 转换 `**bold**`、`*italic*`、`` `code` ``、` ```block``` ` 为 Telegram HTML，最终消息用 `parse_mode=HTML`，失败回退纯文本
- TG Bot 表格转等宽文本：`_table_to_pre()` 将 Markdown 表格转为 `<pre>` 对齐文本（CJK 字符双倍宽度计算）
- TG Bot 图片发送：`tool_end` 事件解析 `content.paths` 字段，自动发送生成的图片
- 修复 `agent_routes.py` `tool_end` 提取：改从 `message` 事件的 `toolResult` block 读取，而非 `current_tool_use_result`
- 抑制 `openai._base_client` 和 `httpcore` DEBUG 日志噪音

### v0.6.0
- 修复 `toolUseEvent` 解析：Kiro 流式分块传输工具调用输入，累积 `input_chunks` 至 `stop=True` 后组装完整 tool_call
- 新增 `_accumulate_tool_use_event()` 处理多块 tool input，替换原错误的 `toolUse` 事件处理
- 修复 shell 工具阻塞：添加 `STRANDS_NON_INTERACTIVE=true` 环境变量，禁用 PTY 和交互确认
- 修复 AWS CLI pager 阻塞：`.env` 添加 `AWS_PAGER=`，子进程继承空值禁用 `less`
- TG Bot 工具调用实时状态：`tool_start` 事件显示 `🔧 name: brief_input...`，`_brief_tool_input()` 按工具类型提取关键参数
- WebUI 聊天改为流式 SSE：`agent_chat_fn` 从阻塞 `httpx.post` 改为 generator + `httpx.stream`，实时渲染工具使用进度
- 修复 `/v1/agent/reload` 500 错误：移除不适用的 `tool_registry.process_tools()` 调用，reload 仅重启 MCP 连接

### v0.5.0
- 修复 Agent 自回环死锁：非流式路径改用 `await invoke_async()`，移除多 worker
- Agent /chat 支持 per-request 切换模型
- 统一 MCP 配置源为 `~/.kiro/settings/mcp.json`，修复 webui 标注错误
- 跳过 http/sse 类型 MCP server（不再崩溃）
- 修复 `mcp.client.stdio` 与 gradio 的循环导入死锁
- Telegram bot 模型列表改为从 `/v1/models` 动态获取

### v0.4.0
- Strands Agent 集成（OpenAIModel 自回环 + MCP 工具）
- Agent API endpoints（/v1/agent/chat 流式 + 非流式）
- TG Bot 改为通过 Agent 层调用
- 内置工具：calculator, file_read, file_write, http_request, shell
- MCP 配置复用 Kiro CLI (~/.kiro/settings/mcp.json)

### v0.3.0
- OpenAI 兼容 API 完整 tool_calls 支持（流式 + 非流式）
- tool role 消息回传 Kiro

### v0.2.0
- Gradio 多页面 Web UI (Navbar)
- 系统配置页 + 监控面板
- TOML 配置文件管理
- 请求统计模块

### v0.1.0
- OpenAI 兼容 API (/v1/chat/completions, /v1/models)
- kiro-cli token 自动刷新
- 流式 + 非流式响应
- Telegram Bot
- 基础 Gradio Web UI
