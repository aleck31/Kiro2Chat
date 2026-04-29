"""Configuration — nested dataclasses mirroring config.toml sections.

Each [section] in config.toml maps to a nested dataclass on `Config`; keys
inside the section are the dataclass fields without any prefix. Access in
code is `config.telegram.bot_token`, `config.acp.kiro_cli_path`, etc.

Field values are resolved via `default_factory` (env var > config.toml >
hard default) so `reload()` picks up on-disk changes without reimporting.
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


def _env(key: str) -> str | None:
    v = os.getenv(key.upper())
    return v if v is not None else None


def _section(name: str) -> dict:
    return _file_cfg.get(name) or {}


def _s(section: str, key: str, default: str = "") -> str:
    """String lookup: env (<SECTION>_<KEY>) > toml[section][key] > default."""
    v = _env(f"{section}_{key}")
    if v is not None:
        return v
    val = _section(section).get(key)
    return str(val) if val is not None else default


def _b(section: str, key: str, default: bool) -> bool:
    v = _env(f"{section}_{key}")
    if v is None:
        raw = _section(section).get(key)
        if raw is None:
            return default
        if isinstance(raw, bool):
            return raw
        v = str(raw)
    return v.strip().lower() not in ("false", "0", "no", "off")


def _i(section: str, key: str, default: int) -> int:
    v = _env(f"{section}_{key}")
    if v is None:
        raw = _section(section).get(key)
        if raw is None:
            return default
        v = str(raw)
    try:
        return int(v)
    except ValueError:
        return default


def _int_ids(section: str, key: str) -> list[int]:
    raw = _section(section).get(key)
    if raw is None:
        env = _env(f"{section}_{key}")
        if env is None:
            return []
        raw = env
    if isinstance(raw, list):
        items = raw
    else:
        items = [p for p in str(raw).split(",") if p.strip()]
    out: list[int] = []
    for p in items:
        try:
            out.append(int(str(p).strip()))
        except ValueError:
            continue
    return out


def _str_ids(section: str, key: str) -> list[str]:
    raw = _section(section).get(key)
    if raw is None:
        env = _env(f"{section}_{key}")
        if env is None:
            return []
        raw = env
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [p.strip() for p in str(raw).split(",") if p.strip()]


# ── Nested section dataclasses ──

@dataclass
class TelegramConfig:
    enabled: bool = field(default_factory=lambda: _b("telegram", "enabled", False))
    bot_token: str = field(default_factory=lambda: _s("telegram", "bot_token"))
    # TG bot handles are fully public (anyone who finds @yourbot can DM it),
    # so authorization is on by default — fail-closed via an empty allowlist.
    require_auth: bool = field(default_factory=lambda: _b("telegram", "require_auth", True))
    allowed_user_ids: list[int] = field(default_factory=lambda: _int_ids("telegram", "allowed_user_ids"))


@dataclass
class LarkConfig:
    enabled: bool = field(default_factory=lambda: _b("lark", "enabled", False))
    app_id: str = field(default_factory=lambda: _s("lark", "app_id"))
    app_secret: str = field(default_factory=lambda: _s("lark", "app_secret"))
    domain: str = field(default_factory=lambda: _s("lark", "domain", "feishu"))
    require_auth: bool = field(default_factory=lambda: _b("lark", "require_auth", False))
    # Lark open_ids (bot-scoped, stable per user) — strings.
    allowed_user_ids: list[str] = field(default_factory=lambda: _str_ids("lark", "allowed_user_ids"))


@dataclass
class DiscordConfig:
    enabled: bool = field(default_factory=lambda: _b("discord", "enabled", False))
    bot_token: str = field(default_factory=lambda: _s("discord", "bot_token"))
    require_auth: bool = field(default_factory=lambda: _b("discord", "require_auth", False))
    allowed_user_ids: list[int] = field(default_factory=lambda: _int_ids("discord", "allowed_user_ids"))


@dataclass
class WebConfig:
    host: str = field(default_factory=lambda: _s("web", "host", "127.0.0.1"))
    port: int = field(default_factory=lambda: _i("web", "port", 7860))


@dataclass
class ACPConfig:
    kiro_cli_path: str = field(default_factory=lambda: _s("acp", "kiro_cli_path", "kiro-cli"))
    workspace_mode: str = field(default_factory=lambda: _s("acp", "workspace_mode", "per_chat"))
    fixed_workspace: str = field(default_factory=lambda: _s("acp", "fixed_workspace", "default"))
    idle_timeout: int = field(default_factory=lambda: _i("acp", "idle_timeout", 300))
    response_timeout: int = field(default_factory=lambda: _i("acp", "response_timeout", 3600))


@dataclass
class Config:
    log_level: str = field(default_factory=lambda: _s("general", "log_level", "info"))
    data_dir: Path = field(default_factory=lambda: Path(
        _s("general", "data_dir") or str(Path.home() / ".local/share/kiro2chat")
    ).expanduser())

    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    lark: LarkConfig = field(default_factory=LarkConfig)
    discord: DiscordConfig = field(default_factory=DiscordConfig)
    web: WebConfig = field(default_factory=WebConfig)
    acp: ACPConfig = field(default_factory=ACPConfig)

    workspaces: dict[str, dict] = field(init=False)

    def __post_init__(self):
        self.workspaces = self._load_workspaces()

    @staticmethod
    def _load_workspaces() -> dict[str, dict]:
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


def reload():
    """Reload config from config.toml."""
    global _file_cfg, config
    _file_cfg = _load_toml()
    config = Config()


# ── First-run bootstrap ──

# Section dataclass attribute name → toml section name.
_TOML_SECTIONS = {
    "telegram": "telegram",
    "lark": "lark",
    "discord": "discord",
    "web": "web",
    "acp": "acp",
}


def _bootstrap_config_file() -> None:
    """Create config.toml from dataclass defaults on first run."""
    from dataclasses import fields, is_dataclass, asdict
    from .config_manager import CONFIG_FILE, save_config_file
    global _file_cfg
    if CONFIG_FILE.exists():
        return
    cfg = Config()
    sections: dict = {}
    for f in fields(cfg):
        v = getattr(cfg, f.name)
        if is_dataclass(v):
            sections[f.name] = asdict(v)
    sections["_workspaces"] = {
        "default": {"path": str(Path.home() / ".local/share/kiro2chat/workspaces/default")},
    }
    save_config_file(sections)
    import logging
    logging.getLogger(__name__).info("📝 Created default config at %s", CONFIG_FILE)
    _file_cfg = _load_toml()


_bootstrap_config_file()
config = Config()
