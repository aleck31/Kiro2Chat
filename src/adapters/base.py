"""Base adapter interface and shared command handling."""


# ── Chat ID ──

def make_chat_id(channel: str, scope: str, raw_id: str | int) -> str:
    """Build a chat_id in unified format: {channel}.{scope}.{raw_id}."""
    return f"{channel}.{scope}.{raw_id}"


# ── Centralized command registry ──

COMMANDS = [
    ("/model", "查看/切换模型"),
    ("/agent", "查看/切换 Agent"),
    ("/workspace", "查看/切换 workspace"),
    ("/context", "查看 context 使用率"),
    ("/cancel", "取消当前操作"),
    ("/reset", "重置会话"),
    ("/help", "帮助"),
]

HELP_TEXT = "\n".join(f"{cmd} — {desc}" for cmd, desc in COMMANDS)


class BaseAdapter:
    """Base class for platform adapters."""

    async def start(self):
        """Start the adapter (connect to platform)."""

    async def stop(self):
        """Stop the adapter."""


def dispatch_command(bridge, chat_id: str, text: str) -> str | None:
    """Handle all shared commands. Returns response text, or None if not a command."""
    lower = text.strip().lower()

    if lower in ("/help",):
        return HELP_TEXT

    if lower == "/cancel":
        bridge.cancel(chat_id)
        return "🛑 Cancelled"

    if lower == "/reset":
        bridge.clear(chat_id)
        return "🗑 会话已重置"

    if lower.startswith("/model"):
        return _handle_model(bridge, chat_id, text)

    if lower.startswith("/agent"):
        return _handle_agent(bridge, chat_id, text)

    if lower.startswith("/workspace"):
        return _handle_workspace(bridge, chat_id, text)

    if lower == "/context":
        return _handle_context(bridge, chat_id)

    return None


# ── Command implementations ──

def _handle_model(bridge, chat_id: str, text: str) -> str:
    parts = text.split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""
    if not arg:
        models = bridge.get_available_models(chat_id)
        current = bridge.get_current_model(chat_id)
        if models:
            lines = []
            for m in models:
                mid = m.get("modelId", m) if isinstance(m, dict) else str(m)
                marker = " ✓" if mid == current else ""
                lines.append(f"• {mid}{marker}")
            body = "\n".join(lines)
        else:
            body = "(先发一条消息开始会话)"
        return f"当前: {current or 'unknown'}\n\n{body}\n\n切换: /model <name>"
    try:
        bridge.set_model(chat_id, arg)
        return f"✅ Model: {arg}"
    except Exception as e:
        return f"❌ {e}"


def _handle_agent(bridge, chat_id: str, text: str) -> str:
    parts = text.split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""
    if not arg:
        modes = bridge.get_available_modes(chat_id)
        current = bridge.get_current_mode(chat_id)
        if modes:
            lines = []
            for m in modes:
                mid = m.get("id", m) if isinstance(m, dict) else str(m)
                marker = " ✓" if mid == current else ""
                lines.append(f"• {mid}{marker}")
            body = "\n".join(lines)
        else:
            body = "(先发一条消息开始会话)"
        return f"当前: {current or 'unknown'}\n\n{body}\n\n切换: /agent <name>"
    try:
        bridge.set_mode(chat_id, arg)
        return f"✅ Agent: {arg}"
    except Exception as e:
        return f"❌ {e}"


def _handle_workspace(bridge, chat_id: str, text: str) -> str:
    parts = text.strip().split(maxsplit=2)
    sub = parts[1] if len(parts) > 1 else ""

    if sub == "list":
        workspaces = bridge.get_workspaces()
        current = bridge.get_active_workspace(chat_id)
        lines = []
        for name, path in workspaces.items():
            marker = " ✓" if name == current else ""
            lines.append(f"• {name}{marker}\n  {path}")
        return "\n".join(lines) or "(no workspaces configured)"

    if sub == "switch" and len(parts) > 2:
        name = parts[2].strip()
        try:
            bridge.switch_workspace(chat_id, name)
            return f"✅ 已切换到 {name}（旧 session 将在空闲后自动释放）"
        except ValueError as e:
            return f"❌ {e}"

    current = bridge.get_active_workspace(chat_id)
    workspaces = bridge.get_workspaces()
    path = workspaces.get(current, "?")
    return f"当前 workspace: {current}\n路径: {path}\n\n/workspace list — 列出所有\n/workspace switch <name> — 切换"


def _handle_context(bridge, chat_id: str) -> str:
    pct = bridge.get_context_usage(chat_id)
    if pct is None:
        return "Context 使用情况暂无数据（发送一条消息后可查看）"
    bar_len = 20
    filled = int(pct / 100 * bar_len)
    bar = "█" * filled + "░" * (bar_len - filled)
    return f"Context 使用率: {pct}%\n[{bar}]"
