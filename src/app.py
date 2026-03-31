"""kiro2chat - Bridge kiro-cli to chat platforms via ACP."""

import asyncio
import logging
import sys

from . import __version__
from .config import config

# Configure logging
from logging.handlers import RotatingFileHandler
from .log_context import UserTagFilter

_log_fmt = "%(asctime)s [%(levelname)s] %(name)s%(user_tag)s: %(message)s"
_user_filter = UserTagFilter()

_console = logging.StreamHandler()
_console.setLevel(getattr(logging, config.log_level.upper(), logging.INFO))
_console.setFormatter(logging.Formatter(_log_fmt))
_console.addFilter(_user_filter)

_log_dir = config.data_dir / "logs"
_log_dir.mkdir(parents=True, exist_ok=True)
_file = RotatingFileHandler(
    _log_dir / "kiro2chat.log", maxBytes=20 * 1024 * 1024, backupCount=10, encoding="utf-8",
)
_file.setLevel(logging.DEBUG)
_file.setFormatter(logging.Formatter(_log_fmt))
_file.addFilter(_user_filter)

logging.basicConfig(level=logging.DEBUG, handlers=[_console, _file])
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.INFO)
logger = logging.getLogger(__name__)


def _create_bridge():
    from .acp.bridge import Bridge
    return Bridge(
        cli_path=config.kiro_cli_path,
        workspace_mode=config.workspace_mode,
        working_dir=config.working_dir,
        idle_timeout=config.idle_timeout,
    )


def run_telegram():
    """Run Telegram bot via ACP bridge."""
    from .adapters.telegram import TelegramAdapter, get_bot_token

    token = get_bot_token()
    if not token:
        logger.error("TG_BOT_TOKEN not set")
        sys.exit(1)

    bridge = _create_bridge()
    bridge.start()
    adapter = TelegramAdapter(bridge, token)
    try:
        asyncio.run(adapter.start())
    except KeyboardInterrupt:
        pass
    finally:
        bridge.stop()


def run_lark():
    """Run Lark/Feishu bot via ACP bridge."""
    from .adapters.lark import LarkAdapter

    if not config.lark_app_id or not config.lark_app_secret:
        logger.error("LARK_APP_ID and LARK_APP_SECRET not set")
        sys.exit(1)

    bridge = _create_bridge()
    bridge.start()
    adapter = LarkAdapter(bridge, config.lark_app_id, config.lark_app_secret, config.lark_domain)
    try:
        asyncio.run(adapter.start())
    except KeyboardInterrupt:
        pass
    finally:
        bridge.stop()


def run_web():
    """Run Web chat UI via ACP bridge."""
    from .adapters.web import WebAdapter

    bridge = _create_bridge()
    bridge.start()
    adapter = WebAdapter(bridge, host=config.web_host, port=config.web_port)
    try:
        adapter.start()
    except KeyboardInterrupt:
        pass
    finally:
        bridge.stop()


def run_discord():
    """Run Discord bot via ACP bridge."""
    from .adapters.discord import DiscordAdapter

    token = config.discord_bot_token
    if not token:
        logger.error("DISCORD_BOT_TOKEN not set")
        sys.exit(1)

    bridge = _create_bridge()
    bridge.start()
    adapter = DiscordAdapter(bridge, token)
    try:
        asyncio.run(adapter.start())
    except KeyboardInterrupt:
        pass
    finally:
        bridge.stop()


USAGE = f"""\
kiro2chat v{__version__} — Bridge kiro-cli to chat platforms via ACP

Usage: kiro2chat <action> [service]

Actions (background via tmux):
  start   [service]  Start in background
  stop    [service]  Stop
  restart [service]  Restart
  status  [service]  Show status
  attach  [service]  Attach to tmux (Ctrl+B D to detach)

Services (default: web):
  web       Web UI (Dashboard + Chat)
  telegram  Telegram Bot
  lark      Lark/Feishu Bot
  discord   Discord Bot

Direct run (foreground):
  kiro2chat web|telegram|lark|discord

Options:
  -h, --help  Show this help
"""

_TMUX_SESSIONS = {
    "telegram": ("kiro2chat-telegram", "uv run kiro2chat telegram"),
    "lark": ("kiro2chat-lark", "uv run kiro2chat lark"),
    "discord": ("kiro2chat-discord", "uv run kiro2chat discord"),
    "web": ("kiro2chat-web", "uv run kiro2chat web"),
}


def _tmux_running(session: str) -> bool:
    import subprocess
    return subprocess.run(["tmux", "has-session", "-t", session], capture_output=True).returncode == 0


def _tmux_start(session: str, cmd: str):
    import subprocess
    from pathlib import Path
    cwd = str(Path(__file__).parent.parent)
    subprocess.run(["tmux", "new-session", "-d", "-s", session, "-c", cwd, cmd], check=True)
    print(f"Started (tmux session: {session})")


def _tmux_stop(session: str):
    import subprocess
    subprocess.run(["tmux", "kill-session", "-t", session], check=True)
    print(f"Stopped (tmux session: {session})")


def _handle_bg(service: str, action: str):
    if service not in _TMUX_SESSIONS:
        print(f"Unknown service: {service}")
        sys.exit(1)
    session, cmd = _TMUX_SESSIONS[service]

    if action == "start":
        if _tmux_running(session):
            print(f"Already running (tmux session: {session})")
            sys.exit(1)
        _tmux_start(session, cmd)
    elif action == "stop":
        if not _tmux_running(session):
            print("Not running")
            sys.exit(1)
        _tmux_stop(session)
    elif action == "restart":
        if _tmux_running(session):
            _tmux_stop(session)
            import time
            time.sleep(1)
        _tmux_start(session, cmd)
    elif action == "status":
        if not _tmux_running(session):
            print(f"{session}: stopped")
        else:
            import subprocess
            pid = subprocess.run(
                ["tmux", "list-panes", "-t", session, "-F", "#{pane_pid}"],
                capture_output=True, text=True,
            ).stdout.strip()
            etime = ""
            if pid:
                r = subprocess.run(["ps", "-o", "etime=", "-p", pid], capture_output=True, text=True)
                etime = r.stdout.strip()
            lines = [f"{session}: running"]
            if etime:
                lines.append(f"  uptime: {etime}")
            if pid:
                lines.append(f"  pid:    {pid}")
            print("\n".join(lines))
    elif action == "attach":
        import os
        os.execvp("tmux", ["tmux", "attach", "-t", session])
    else:
        print(f"Unknown action: {action}")
        sys.exit(1)


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help", "help"):
        print(USAGE)
        return

    _BG_ACTIONS = {"start", "stop", "restart", "status", "attach"}
    _SERVICES = set(_TMUX_SESSIONS.keys())

    # kiro2chat <action> [service]
    if args[0] in _BG_ACTIONS:
        service = args[1] if len(args) > 1 and args[1] in _SERVICES else "web"
        _handle_bg(service, args[0])
        return

    # kiro2chat <service> <action>
    if args[0] in _SERVICES and len(args) > 1 and args[1] in _BG_ACTIONS:
        _handle_bg(args[0], args[1])
        return

    # foreground run
    if args[0] == "telegram":
        run_telegram()
    elif args[0] == "lark":
        run_lark()
    elif args[0] == "web":
        run_web()
    elif args[0] == "discord":
        run_discord()
    else:
        print(f"Unknown command: {args[0]}\n")
        print(USAGE)
        sys.exit(1)


if __name__ == "__main__":
    main()
