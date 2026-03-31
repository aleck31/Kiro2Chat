"""Config file management for kiro2chat."""

from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "kiro2chat"
CONFIG_FILE = CONFIG_DIR / "config.toml"
KIRO_MCP_CONFIG = Path.home() / ".kiro" / "settings" / "mcp.json"

# Flat key -> TOML section mapping
_SECTIONS = {
    "log_level": "general",
    "data_dir": "general",
    "tg_bot_token": "telegram",
    "lark_app_id": "lark",
    "lark_app_secret": "lark",
    "lark_domain": "lark",
    "discord_bot_token": "discord",
    "web_host": "web",
    "web_port": "web",
    "kiro_cli_path": "acp",
    "workspace_mode": "acp",
    "working_dir": "acp",
    "idle_timeout": "acp",
}


def load_config_file() -> dict:
    """Read config from TOML file, return flat dict."""
    if not CONFIG_FILE.exists():
        return {}

    import tomllib

    with open(CONFIG_FILE, "rb") as f:
        data = tomllib.load(f)

    # Flatten sections (one level deep; nested dicts like model.model_map stay as dicts)
    flat: dict = {}
    for section_key, section_data in data.items():
        if isinstance(section_data, dict):
            for k, v in section_data.items():
                flat[k] = v
        else:
            flat[section_key] = section_data
    return flat


def save_config_file(flat: dict) -> None:
    """Write flat config dict to TOML file with sections."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    # Group by section
    sections: dict[str, dict] = {}
    for key, value in flat.items():
        if value is None or value == "":
            continue
        section = _SECTIONS.get(key, "general")
        sections.setdefault(section, {})[key] = value

    # Write TOML manually (avoid extra dep at import time)
    lines: list[str] = []
    for section, kvs in sections.items():
        lines.append(f"[{section}]")
        for k, v in kvs.items():
            if isinstance(v, dict):
                # Write dict as separate sub-table
                lines.append("")
                lines.append(f"[{section}.{k}]")
                for dk, dv in v.items():
                    lines.append(f'"{dk}" = "{dv}"')
                continue
            elif isinstance(v, list):
                items = ", ".join(f'"{i}"' for i in v)
                lines.append(f"{k} = [{items}]")
            elif isinstance(v, int):
                lines.append(f"{k} = {v}")
            elif isinstance(v, bool):
                lines.append(f"{k} = {'true' if v else 'false'}")
            else:
                lines.append(f'{k} = "{v}"')
        lines.append("")

    CONFIG_FILE.write_text("\n".join(lines), encoding="utf-8")


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
