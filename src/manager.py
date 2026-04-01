"""Adapter lifecycle manager — start/stop adapters within the same process."""

import asyncio
import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class AdapterState:
    name: str
    status: str = "stopped"  # running | stopped | unconfigured
    started_at: float = 0
    task: asyncio.Task | None = field(default=None, repr=False)


class AdapterManager:
    """Manages adapter lifecycle within a single asyncio event loop."""

    def __init__(self):
        self._adapters: dict[str, AdapterState] = {}
        self._bridge = None

    @property
    def bridge(self):
        return self._bridge

    def init(self, bridge):
        self._bridge = bridge
        for name in ("telegram", "lark", "discord"):
            self._adapters[name] = AdapterState(name=name, status="unconfigured")
        self._detect_configured()

    def _detect_configured(self):
        """Mark adapters as stopped if tokens are configured."""
        from .config import config
        if config.tg_bot_token:
            self._adapters["telegram"].status = "stopped"
        if config.lark_app_id and config.lark_app_secret:
            self._adapters["lark"].status = "stopped"
        if config.discord_bot_token:
            self._adapters["discord"].status = "stopped"

    def _auto_start(self):
        """Auto-start all configured adapters."""
        for name, state in self._adapters.items():
            if state.status == "stopped":
                try:
                    self.start_adapter(name)
                except Exception as e:
                    logger.error("[Manager] Auto-start %s failed: %s", name, e)

    def get_states(self) -> dict[str, dict]:
        now = time.time()
        return {
            name: {
                "status": s.status,
                "uptime": int(now - s.started_at) if s.status == "running" else 0,
            }
            for name, s in self._adapters.items()
        }

    def start_adapter(self, name: str):
        state = self._adapters.get(name)
        if not state or state.status == "running":
            return
        if state.status == "unconfigured":
            raise ValueError(f"{name}: not configured (missing token)")

        async def _run():
            try:
                state.status = "running"
                state.started_at = time.time()
                logger.info("[Manager] Starting %s", name)
                await self._start_adapter(name)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error("[Manager] %s crashed: %s", name, e)
            finally:
                state.status = "stopped"
                state.task = None

        state.task = asyncio.ensure_future(_run())

    def stop_adapter(self, name: str):
        state = self._adapters.get(name)
        if not state or state.status != "running" or not state.task:
            return
        state.task.cancel()
        state.status = "stopped"
        state.task = None
        logger.info("[Manager] Stopped %s", name)

    async def _start_adapter(self, name: str):
        from .config import config
        if name == "telegram":
            from .adapters.telegram import TelegramAdapter
            adapter = TelegramAdapter(self._bridge, config.tg_bot_token)
            await adapter.start()
        elif name == "lark":
            from .adapters.lark import LarkAdapter
            adapter = LarkAdapter(self._bridge, config.lark_app_id, config.lark_app_secret, config.lark_domain)
            await adapter.start()
        elif name == "discord":
            from .adapters.discord import DiscordAdapter
            adapter = DiscordAdapter(self._bridge, config.discord_bot_token)
            await adapter.start()

    def refresh_config(self):
        """Re-detect configured adapters after config change."""
        from .config import reload
        reload()
        self._detect_configured()


manager = AdapterManager()
