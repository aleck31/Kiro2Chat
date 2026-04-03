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

Usage:
  kiro2chat                     Start daemon (backend + web console)
  kiro2chat daemon              Same as above
  kiro2chat adapter <name>      Start a single adapter standalone

Adapters:
  telegram    Telegram Bot
  lark        Lark/Feishu Bot
  discord     Discord Bot
  web         Web Chat (standalone, without dashboard)

Options:
  -h, --help  Show this help
"""


def main():
    args = sys.argv[1:]
    if not args or args[0] == "daemon":
        run_web()
        return
    if args[0] in ("-h", "--help", "help"):
        print(USAGE)
        return
    if args[0] == "adapter" and len(args) > 1:
        adapters = {"telegram": run_telegram, "lark": run_lark, "discord": run_discord, "web": run_web}
        name = args[1]
        if name in adapters:
            adapters[name]()
        else:
            print(f"Unknown adapter: {name}")
            sys.exit(1)
        return

    print(f"Unknown command: {args[0]}\n")
    print(USAGE)
    sys.exit(1)


if __name__ == "__main__":
    main()
