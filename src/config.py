"""Configuration management — reads from config.toml only."""

import os
from pathlib import Path
from dataclasses import dataclass


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


def reload():
    """Reload config from config.toml."""
    global _file_cfg, config
    _file_cfg = _load_toml()
    config = Config()


@dataclass
class Config:
    log_level: str = _get("log_level") or "info"

    data_dir: Path = Path(
        _get("data_dir") or str(Path.home() / ".local/share/kiro2chat")
    ).expanduser()

    # Telegram
    tg_bot_token: str = _get("tg_bot_token") or ""

    # Lark/Feishu
    lark_app_id: str = _get("lark_app_id") or ""
    lark_app_secret: str = _get("lark_app_secret") or ""
    lark_domain: str = _get("lark_domain") or "feishu"

    # Discord
    discord_bot_token: str = _get("discord_bot_token") or ""

    # Web UI
    web_host: str = _get("web_host") or "127.0.0.1"
    web_port: int = int(_get("web_port") or "7860")

    # ACP
    kiro_cli_path: str = _get("kiro_cli_path") or "kiro-cli"
    workspace_mode: str = _get("workspace_mode") or "per_chat"
    working_dir: str = _get("working_dir") or str(
        Path.home() / ".local/share/kiro2chat/workspaces"
    )
    idle_timeout: int = int(_get("idle_timeout") or "300")


config = Config()
