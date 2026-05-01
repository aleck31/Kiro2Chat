"""WebServer — hosts NiceGUI, assembles all pages, runs adapter manager.

Responsibility split:
- `WebServer`  : owns `ui.run()`, page registration, startup hook.
- `WebAdapter` : the chat-platform adapter (send/receive/permission), symmetric
                 with Telegram / Lark / Discord adapters.
- `AdapterManager` : starts configured bot adapters inside the same asyncio loop.
"""

import asyncio
import logging

from nicegui import app, ui

from .acp.bridge import Bridge
from .adapters.web import WebAdapter
from .manager import manager
from .scheduler import Scheduler
from .webui import register_pages

logger = logging.getLogger(__name__)

# Module-level handle so the scheduler can broadcast to /chat tabs without
# WebAdapter being in the AdapterManager (it's built into the server).
_web_adapter_ref: WebAdapter | None = None
_scheduler: Scheduler | None = None


def get_scheduler() -> Scheduler | None:
    return _scheduler


def dashboard_urls(host: str, port: int) -> list[str]:
    """Resolve the URLs a user can click to reach the dashboard.

    For specific hosts (127.0.0.1, a LAN IP, a hostname) we just return
    http://<host>:<port>. For wildcard binds (0.0.0.0 / ::) we enumerate
    loopback + every non-loopback interface so remote users see the
    right address.
    """
    if host not in ("0.0.0.0", "::", ""):
        return [f"http://{host}:{port}"]

    import socket
    urls = [f"http://127.0.0.1:{port}   (local)"]
    seen = {"127.0.0.1"}
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, family=socket.AF_INET):
            ip = info[4][0]
            if not isinstance(ip, str) or ip.startswith("127.") or ip in seen:
                continue
            seen.add(ip)
            urls.append(f"http://{ip}:{port}   (network)")
    except OSError:
        pass
    return urls


def _print_dashboard_banner(host: str, port: int):
    urls = dashboard_urls(host, port)
    print(f"🚀 Dashboard: {urls[0]}")
    for u in urls[1:]:
        print(f"              {u}")


def _patch_storage_indent():
    """Make NiceGUI write storage-user-*.json with pretty indentation.

    NiceGUI's FilePersistentDict supports `indent=True`, but Storage._create_persistent_dict
    doesn't expose it — we patch the factory to pass it through for filesystem storage.
    """
    from nicegui import storage as _storage
    from nicegui.persistence import FilePersistentDict

    original = _storage.Storage._create_persistent_dict

    @staticmethod
    def _create(id: str):  # noqa: A002
        if _storage.Storage.redis_url:
            return original(id)
        return FilePersistentDict(
            _storage.Storage.path / f"storage-{id}.json",
            encoding="utf-8",
            indent=True,
        )

    _storage.Storage._create_persistent_dict = _create


class WebServer:
    def __init__(self, bridge: Bridge, host: str = "127.0.0.1", port: int = 7860):
        self._bridge = bridge
        self._host = host
        self._port = port
        self._web_adapter = WebAdapter(bridge)

    def run(self):
        _patch_storage_indent()
        manager.init(self._bridge)
        register_pages(self._bridge, self._web_adapter)

        async def _on_startup():
            global _web_adapter_ref, _scheduler
            self._web_adapter.bind_loop(asyncio.get_running_loop())
            _web_adapter_ref = self._web_adapter
            await self._web_adapter.start()
            manager._auto_start()
            from .config import config
            _scheduler = Scheduler(self._bridge, manager)
            _scheduler.start(config.tasks)
            _print_dashboard_banner(self._host, self._port)

        async def _on_shutdown():
            if _scheduler:
                _scheduler.stop()

        app.on_shutdown(_on_shutdown)

        app.on_startup(_on_startup)

        ui.run(
            host=self._host,
            port=self._port,
            title="Kiro2Chat",
            storage_secret="kiro2chat-web",
            show=False,
            reload=False,
        )
