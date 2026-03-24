"""Configuration management."""

import os
from pathlib import Path
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


def _load_toml_defaults() -> dict:
    try:
        from .config_manager import load_config_file
        return load_config_file()
    except Exception:
        return {}


_file_cfg = _load_toml_defaults()


def _get(key: str, env_key: str | None = None) -> str | None:
    env = env_key or key.upper()
    val = os.getenv(env)
    if val is not None:
        return val
    val = _file_cfg.get(key)
    if val is not None:
        return str(val)
    return None


@dataclass
class Config:
    log_level: str = _get("log_level", "LOG_LEVEL") or "info"

    # Data directory (logs, workspaces, etc.)
    data_dir: Path = Path(
        _get("data_dir", "KIRO2CHAT_DATA_DIR")
        or str(Path.home() / ".local/share/kiro2chat")
    ).expanduser()

    # Telegram bot
    tg_bot_token: str | None = _get("tg_bot_token", "TG_BOT_TOKEN")

    # Lark/Feishu bot
    lark_app_id: str = _get("lark_app_id", "LARK_APP_ID") or ""
    lark_app_secret: str = _get("lark_app_secret", "LARK_APP_SECRET") or ""
    lark_domain: str = _get("lark_domain", "LARK_DOMAIN") or "feishu"  # feishu | lark

    # Web UI
    web_host: str = _get("web_host", "WEB_HOST") or "127.0.0.1"
    web_port: int = int(_get("web_port", "WEB_PORT") or "8080")

    # ACP settings
    kiro_cli_path: str = _get("kiro_cli_path", "KIRO_CLI_PATH") or "kiro-cli"
    workspace_mode: str = _get("workspace_mode", "WORKSPACE_MODE") or "per_chat"
    working_dir: str = _get("working_dir", "WORKING_DIR") or str(
        Path.home() / ".local/share/kiro2chat/workspaces"
    )
    idle_timeout: int = int(_get("idle_timeout", "IDLE_TIMEOUT") or "300")


config = Config()
