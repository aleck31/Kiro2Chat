"""Config file management for kiro2chat.

TOML is read/written as a section-keyed dict: {"telegram": {...}, "acp": {...}, ...}.
The `_workspaces` key is preserved as-is for `[workspaces.<name>]` subtables.
"""

from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "kiro2chat"
CONFIG_FILE = CONFIG_DIR / "config.toml"
KIRO_MCP_CONFIG = Path.home() / ".kiro" / "settings" / "mcp.json"


def load_config_file() -> dict:
    """Read config.toml as {section: {key: value}}, plus `_workspaces` dict."""
    if not CONFIG_FILE.exists():
        return {}

    import tomllib

    with open(CONFIG_FILE, "rb") as f:
        data = tomllib.load(f)

    result: dict = {}
    for section_key, section_data in data.items():
        if section_key == "workspaces" and isinstance(section_data, dict):
            result["_workspaces"] = section_data
        elif section_key == "tasks" and isinstance(section_data, list):
            # `[[tasks]]` array-of-tables — preserve as a list of dicts.
            result["tasks"] = section_data
        elif isinstance(section_data, dict):
            result[section_key] = section_data
        else:
            # Stray top-level key — stash under "general" for round-trip safety.
            result.setdefault("general", {})[section_key] = section_data
    return result


def save_config_file(sections: dict) -> None:
    """Write a section-keyed dict back to config.toml.

    `sections` is shaped like {"telegram": {...}, "_workspaces": {...}}.
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    workspaces = sections.pop("_workspaces", None)
    tasks = sections.pop("tasks", None)

    lines: list[str] = []
    for section, kvs in sections.items():
        if not isinstance(kvs, dict):
            continue
        # Skip empty sections to keep the file tidy on first save.
        scalar_items = [(k, v) for k, v in kvs.items()
                        if v is not None and v != "" and not isinstance(v, dict)]
        dict_items = [(k, v) for k, v in kvs.items() if isinstance(v, dict)]
        if not scalar_items and not dict_items:
            continue

        lines.append(f"[{section}]")
        for k, v in scalar_items:
            lines.append(_fmt_kv(k, v))
        lines.append("")
        for k, v in dict_items:
            lines.append(f"[{section}.{k}]")
            for dk, dv in v.items():
                lines.append(_fmt_kv(dk, dv))
            lines.append("")

    if workspaces and isinstance(workspaces, dict):
        for name, val in workspaces.items():
            if isinstance(val, dict):
                path = val.get("path", "")
                sid = val.get("session_id", "")
            else:
                path = str(val)
                sid = ""
            lines.append(f"[workspaces.{name}]")
            lines.append(f'path = "{path}"')
            if sid:
                lines.append(f'session_id = "{sid}"')
            lines.append("")

    if tasks and isinstance(tasks, list):
        for t in tasks:
            if not isinstance(t, dict):
                continue
            lines.append("[[tasks]]")
            for k, v in t.items():
                lines.append(_fmt_kv(k, v))
            lines.append("")

    CONFIG_FILE.write_text("\n".join(lines), encoding="utf-8")


def _fmt_kv(k: str, v) -> str:
    if isinstance(v, bool):
        return f"{k} = {'true' if v else 'false'}"
    if isinstance(v, int):
        return f"{k} = {v}"
    if isinstance(v, list):
        def _item(i):
            if isinstance(i, bool):
                return "true" if i else "false"
            if isinstance(i, int):
                return str(i)
            return f'"{i}"'
        return f"{k} = [{', '.join(_item(i) for i in v)}]"
    return f'{k} = "{v}"'


def load_mcp_config() -> dict:
    """Load MCP server configuration from Kiro CLI's config (~/.kiro/settings/mcp.json)."""
    import json
    if not KIRO_MCP_CONFIG.exists():
        return {"mcpServers": {}}
    try:
        return json.loads(KIRO_MCP_CONFIG.read_text())
    except Exception:
        return {"mcpServers": {}}


def save_mcp_config(config: dict) -> None:
    """Save MCP server configuration to Kiro CLI's config (~/.kiro/settings/mcp.json)."""
    import json
    KIRO_MCP_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    KIRO_MCP_CONFIG.write_text(json.dumps(config, indent=2, ensure_ascii=False))
