"""Adapter lifecycle manager — start/stop adapters within the same process."""

import asyncio
import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class AdapterState:
    name: str
    status: str = "stopped"  # running | stopped | disabled | unconfigured
    enabled: bool = True
    started_at: float = 0
    task: asyncio.Task | None = field(default=None, repr=False)


class AdapterManager:
    """Manages adapter lifecycle within a single asyncio event loop."""

    def __init__(self):
        self._adapters: dict[str, AdapterState] = {}
        # Live adapter instances — kept so stop_adapter() can call their stop()
        # (manager-level task.cancel() alone is not enough for aiogram polling,
        # lark websocket thread, or discord client — they hold external state).
        self._instances: dict[str, object] = {}
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
        """Mark each adapter's status based on config.

        Precedence (by design):
        - disabled:     enabled=false (takes priority — user-controlled gate)
        - unconfigured: enabled=true  but missing credentials
        - stopped:      enabled=true  and credentials present
        """
        from .config import config
        specs = [
            ("telegram", bool(config.tg_bot_token), config.tg_enabled),
            ("lark",     bool(config.lark_app_id and config.lark_app_secret), config.lark_enabled),
            ("discord",  bool(config.discord_bot_token), config.discord_enabled),
        ]
        for name, configured, enabled in specs:
            state = self._adapters[name]
            state.enabled = enabled
            # Don't clobber running adapters — config reload while running.
            if state.status == "running":
                continue
            if not enabled:
                state.status = "disabled"
            elif not configured:
                state.status = "unconfigured"
            else:
                state.status = "stopped"

    def _auto_start(self):
        """Auto-start all enabled+configured adapters."""
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
                "enabled": s.enabled,
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
        if state.status == "disabled":
            raise ValueError(f"{name}: disabled in settings")

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
                self._instances.pop(name, None)

        state.task = asyncio.ensure_future(_run())

    def stop_adapter(self, name: str):
        state = self._adapters.get(name)
        if not state or state.status != "running" or not state.task:
            return
        # Ask the adapter to release external resources (TG polling, Lark WS,
        # Discord client). Schedule as a task so we don't block the caller.
        adapter = self._instances.pop(name, None)
        if adapter and hasattr(adapter, "stop"):
            try:
                asyncio.ensure_future(adapter.stop())
            except Exception as e:
                logger.warning("[Manager] %s.stop() scheduling failed: %s", name, e)
        state.task.cancel()
        state.status = "stopped"
        state.task = None
        logger.info("[Manager] Stopped %s", name)

    def _build_adapter(self, name: str):
        from .config import config
        if name == "telegram":
            from .adapters.telegram import TelegramAdapter
            return TelegramAdapter(self._bridge, config.tg_bot_token)
        if name == "lark":
            from .adapters.lark import LarkAdapter
            return LarkAdapter(self._bridge, config.lark_app_id, config.lark_app_secret, config.lark_domain)
        if name == "discord":
            from .adapters.discord import DiscordAdapter
            return DiscordAdapter(self._bridge, config.discord_bot_token)
        raise ValueError(f"Unknown adapter: {name}")

    async def _start_adapter(self, name: str):
        adapter = self._build_adapter(name)
        self._instances[name] = adapter
        await adapter.start()

    def refresh_config(self):
        """Re-detect configured adapters after config change."""
        from .config import reload
        reload()
        self._detect_configured()


manager = AdapterManager()
