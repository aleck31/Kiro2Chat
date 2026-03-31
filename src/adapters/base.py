"""Base adapter interface for chat platforms."""


class BaseAdapter:
    """Base class for platform adapters."""

    async def start(self):
        """Start the adapter (connect to platform)."""

    async def stop(self):
        """Stop the adapter."""


def handle_workspace_command(bridge, chat_id: str, text: str) -> str | None:
    """Handle /workspace commands. Returns response text or None if not a workspace command."""
    lower = text.strip().lower()
    if not lower.startswith("/workspace"):
        return None

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

    # Default: show current
    current = bridge.get_active_workspace(chat_id)
    workspaces = bridge.get_workspaces()
    path = workspaces.get(current, "?")
    return f"当前 workspace: {current}\n路径: {path}\n\n/workspace list — 列出所有\n/workspace switch <name> — 切换"
