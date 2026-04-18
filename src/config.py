"""Configuration management — reads from config.toml only.

Config is a dataclass whose fields are (re-)populated via `default_factory` so
that `reload()` picks up on-disk changes. Using plain default values would
freeze them at class-definition time.
"""

import os
from pathlib import Path
from dataclasses import dataclass, field


def _load_toml() -> dict:
    try:
        from .config_manager import load_config_file
        return load_config_file()
    except Exception:
        return {}


_file_cfg = _load_toml()


def _get(key: str) -> str | None:
    """Get config value: env var (uppercase key) > config.toml."""
    val = os.getenv(key.upper())
    if val is not None:
        return val
    val = _file_cfg.get(key)
    if val is not None:
        return str(val)
    return None


def _bool(key: str, default: bool = True) -> bool:
    v = _get(key)
    if v is None:
        return default
    return v.strip().lower() not in ("false", "0", "no", "off")


def reload():
    """Reload config from config.toml."""
    global _file_cfg, config
    _file_cfg = _load_toml()
    config = Config()


@dataclass
class Config:
    log_level: str = field(default_factory=lambda: _get("log_level") or "info")

    data_dir: Path = field(default_factory=lambda: Path(
        _get("data_dir") or str(Path.home() / ".local/share/kiro2chat")
    ).expanduser())

    # Telegram
    tg_bot_token: str = field(default_factory=lambda: _get("tg_bot_token") or "")
    tg_enabled: bool = field(default_factory=lambda: _bool("tg_enabled"))

    # Lark/Feishu
    lark_app_id: str = field(default_factory=lambda: _get("lark_app_id") or "")
    lark_app_secret: str = field(default_factory=lambda: _get("lark_app_secret") or "")
    lark_domain: str = field(default_factory=lambda: _get("lark_domain") or "feishu")
    lark_enabled: bool = field(default_factory=lambda: _bool("lark_enabled"))

    # Discord
    discord_bot_token: str = field(default_factory=lambda: _get("discord_bot_token") or "")
    discord_enabled: bool = field(default_factory=lambda: _bool("discord_enabled"))

    # Web UI
    web_host: str = field(default_factory=lambda: _get("web_host") or "127.0.0.1")
    web_port: int = field(default_factory=lambda: int(_get("web_port") or "7860"))

    # ACP
    kiro_cli_path: str = field(default_factory=lambda: _get("kiro_cli_path") or "kiro-cli")
    workspace_mode: str = field(default_factory=lambda: _get("workspace_mode") or "per_chat")
    # In fixed mode, all chats share this workspace (by name, resolved from [workspaces]).
    fixed_workspace: str = field(default_factory=lambda: _get("fixed_workspace") or "default")
    idle_timeout: int = field(default_factory=lambda: int(_get("idle_timeout") or "300"))
    response_timeout: int = field(default_factory=lambda: int(_get("response_timeout") or "3600"))

    # Workspaces: name → path
    @staticmethod
    def _load_workspaces() -> dict[str, dict]:
        """Load workspaces: {name: {path: str, session_id: str|None}}."""
        ws = _file_cfg.get("_workspaces", {})
        result = {}
        for name, val in ws.items():
            if isinstance(val, dict):
                result[name] = {"path": val.get("path", ""), "session_id": val.get("session_id")}
            else:
                result[name] = {"path": str(val), "session_id": None}
        if not result:
            default_path = str(Path.home() / ".local/share/kiro2chat/workspaces/default")
            result = {"default": {"path": default_path, "session_id": None}}
        return result

    def __post_init__(self):
        self.workspaces: dict[str, dict] = self._load_workspaces()


config = Config()
