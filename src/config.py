"""Configuration management."""

import os
from pathlib import Path
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


def _load_toml_defaults() -> dict:
    """Load defaults from config.toml (if exists)."""
    try:
        from .config_manager import load_config_file
        return load_config_file()
    except Exception:
        return {}


_file_cfg = _load_toml_defaults()


def _get(key: str, env_key: str | None = None) -> str | None:
    """Get config value: env var > toml file."""
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
    port: int = int(_get("port", "PORT") or "8000")
    host: str = _get("host", "HOST") or "0.0.0.0"
    api_key: str | None = os.getenv("API_KEY")
    log_level: str = _get("log_level", "LOG_LEVEL") or "info"

    # kiro-cli SQLite database path
    kiro_db_path: str = str(Path(
        _get("kiro_db_path", "KIRO_DB_PATH")
        or str(Path.home() / ".local/share/kiro-cli/data.sqlite3")
    ).expanduser())

    # Telegram bot
    tg_bot_token: str | None = _get("tg_bot_token", "TG_BOT_TOKEN")

    # Data directory (logs, output, etc.)
    data_dir: Path = Path(
        _get("data_dir", "KIRO2CHAT_DATA_DIR")
        or str(Path.home() / ".local/share/kiro2chat")
    ).expanduser()

    # AWS endpoints
    idc_refresh_url: str = (
        _get("idc_refresh_url", "IDC_REFRESH_URL")
        or "https://oidc.us-east-1.amazonaws.com/token"
    )
    kiro_api_endpoint: str = (
        _get("kiro_api_endpoint", "KIRO_API_ENDPOINT")
        or "https://codewhisperer.us-east-1.amazonaws.com/generateAssistantResponse"
    )

    # Kiro profile ARN (read from kiro-cli state)
    profile_arn: str = os.getenv("PROFILE_ARN", "")

    # Model mapping: OpenAI model name -> Kiro model ID
    # config.toml > hardcoded defaults
    model_map: dict[str, str] = field(default_factory=lambda: _file_cfg.get("model_map") or {
        # Opus 4.6
        "claude-opus-4-6 (Krio)": "claude-opus-4.6",
        # Sonnet 4.6
        "claude-sonnet-4-6 (Krio)": "claude-sonnet-4.6",
        # Opus 4.5
        "claude-opus-4-5 (Krio)": "claude-opus-4.5",
        # Sonnet 4.5
        "claude-sonnet-4-5 (Krio)": "CLAUDE_SONNET_4_5_20250929_V1_0",
        # Haiku 4.5
        "claude-haiku-4-5 (Krio)": "claude-haiku-4.5",
        # Sonnet 4
        "claude-sonnet-4 (Krio)": "CLAUDE_SONNET_4_20250514_V1_0"
    })

    # Default model when client doesn't specify
    default_model: str = _get("default_model", "DEFAULT_MODEL") or "claude-sonnet-4-6 (Krio)"

    # Assistant identity presented to users: "kiro" (default) or "claude"
    assistant_identity: str = _file_cfg.get("assistant_identity") or "kiro"

    # Context size limit (tokens) — requests exceeding this are rejected before sending
    context_limit: int = int(_file_cfg.get("context_limit") or 190_000)

    # ── ACP settings ──
    kiro_cli_path: str = _get("kiro_cli_path", "KIRO_CLI_PATH") or "kiro-cli"
    workspace_mode: str = _get("workspace_mode", "WORKSPACE_MODE") or "per_chat"
    working_dir: str = _get("working_dir", "WORKING_DIR") or str(
        Path.home() / ".local/share/kiro2chat/workspaces"
    )
    idle_timeout: int = int(_get("idle_timeout", "IDLE_TIMEOUT") or "300")

config = Config()
