"""kiro2chat - Bridge kiro-cli to chat platforms via ACP."""

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
        fixed_workspace=config.fixed_workspace,
        idle_timeout=config.idle_timeout,
    )


def run_web():
    """Run Web chat UI + admin dashboard via ACP bridge.

    AdapterManager auto-starts each adapter whose `*_enabled` flag is true,
    so this single entry point covers all platforms.
    """
    from .server import WebServer

    bridge = _create_bridge()
    bridge.start()
    server = WebServer(bridge, host=config.web_host, port=config.web_port)
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    finally:
        bridge.stop()


USAGE = f"""\
kiro2chat v{__version__} — Bridge kiro-cli to chat platforms via ACP

Usage:
  kiro2chat run                 Run in foreground (used by systemd; dev/debug)
  kiro2chat install             Install & enable the systemd user service
  kiro2chat uninstall           Disable & remove the systemd user service
  kiro2chat [action]            Daemon management via systemd
    start                       Start daemon
    stop                        Stop daemon
    restart                     Restart daemon
    status                      Show daemon status

Configure which adapters run via the dashboard at http://<host>:7860/settings.
"""

_SERVICE = "kiro2chat.service"


def _service_unit_path():
    from pathlib import Path
    return Path.home() / ".config/systemd/user" / _SERVICE


def _service_is_installed() -> bool:
    return _service_unit_path().is_file()


def _systemctl(action: str):
    """Proxy daemon actions to systemctl --user."""
    import subprocess
    if action in ("start", "stop", "restart", "status") and not _service_is_installed():
        print(f"❌ {_SERVICE} is not installed.")
        print("   Run `kiro2chat install` first.")
        return 1
    result = subprocess.run(["systemctl", "--user", action, _SERVICE], capture_output=True, text=True)
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.stderr.strip():
        print(result.stderr.strip())
    return result.returncode


def install_service() -> int:
    """Generate the unit file from deploy/kiro2chat.service template and enable it."""
    import shutil
    import subprocess
    from pathlib import Path

    project_dir = Path(__file__).resolve().parent.parent
    template = Path(__file__).resolve().parent / "systemd" / "kiro2chat.service"
    if not template.is_file():
        print(f"❌ Template not found: {template}")
        return 1

    uv_path = shutil.which("uv") or str(Path.home() / ".local/bin/uv")
    unit_path = _service_unit_path()
    unit_path.parent.mkdir(parents=True, exist_ok=True)

    content = (template.read_text()
               .replace("__WORKING_DIR__", str(project_dir))
               .replace("__UV_PATH__", uv_path))
    unit_path.write_text(content)
    print(f"✅ Wrote {unit_path}")
    print(f"   Project: {project_dir}")
    print(f"   uv:      {uv_path}")

    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    rc = subprocess.run(["systemctl", "--user", "enable", _SERVICE], check=False).returncode
    if rc == 0:
        print(f"✅ Enabled {_SERVICE}. Start with `kiro2chat start`.")
    return rc


def uninstall_service() -> int:
    """Disable the systemd user service and remove the unit file."""
    import subprocess
    unit_path = _service_unit_path()
    if not unit_path.is_file():
        print(f"{_SERVICE} is not installed — nothing to do.")
        return 0
    subprocess.run(["systemctl", "--user", "stop", _SERVICE], check=False, capture_output=True)
    subprocess.run(["systemctl", "--user", "disable", _SERVICE], check=False, capture_output=True)
    unit_path.unlink(missing_ok=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    print(f"✅ Removed {unit_path}")
    return 0


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help", "help"):
        print(USAGE)
        return

    _DAEMON_ACTIONS = {"start", "stop", "restart", "status"}

    if args[0] in ("run", "daemon"):
        # `daemon` is a legacy alias for `run` (foreground).
        run_web()
        return
    if args[0] == "install":
        sys.exit(install_service())
    if args[0] == "uninstall":
        sys.exit(uninstall_service())
    if args[0] in _DAEMON_ACTIONS:
        sys.exit(_systemctl(args[0]))

    print(f"Unknown command: {args[0]}\n")
    print(USAGE)
    sys.exit(1)


if __name__ == "__main__":
    main()
