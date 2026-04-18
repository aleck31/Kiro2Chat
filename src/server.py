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
from .webui import register_pages

logger = logging.getLogger(__name__)


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
            self._web_adapter.bind_loop(asyncio.get_running_loop())
            await self._web_adapter.start()
            manager._auto_start()

        app.on_startup(_on_startup)

        ui.run(
            host=self._host,
            port=self._port,
            title="Kiro2Chat",
            storage_secret="kiro2chat-web",
            show=False,
            reload=False,
        )
