"""Scheduled-task runner — pushes kiro-generated messages to chat platforms.

Each TaskConfig fires every N seconds with a fixed prompt, runs it through the ACP bridge in the task's workspace, 
then fan-outs the answer to the configured platform + chat_ids.

Design choices:
- One asyncio.Task per scheduled task; each sleeps its own interval so a slow run doesn't skew siblings.
- First run waits for the first interval (never on startup).
- If the workspace's ACP session is busy (human chatting), we queue — bridge.prompt() itself serializes on a per-workspace lock, so we just
  await it and it'll run when the chat finishes. Dropping the task would silently swallow a scheduled broadcast, which is worse than a delay.
- Allowlist enforcement: for platforms with auth, targets must be in the section's allowlist; unknown ids are dropped with a WARNING. Empty
  target_chat_ids = broadcast to the whole allowlist (webchat broadcasts to every open /chat tab since it has no allowlist).
- Per-target pause (0.5s) to stay under platform rate limits.
"""

import asyncio
import logging
import time
from datetime import datetime

from .config import TaskConfig

logger = logging.getLogger(__name__)

BROADCAST_PAUSE = 0.5  # seconds between targets in a single run


def next_fire_at(task: TaskConfig, now: float | None = None) -> float | None:
    """Seconds-since-epoch of the next firing time for `task`, or None if
    the task is malformed. `cron` wins over `every_seconds` when both are set."""
    now = now if now is not None else time.time()
    if task.cron:
        try:
            from croniter import croniter
            it = croniter(task.cron, datetime.fromtimestamp(now))
            return it.get_next(float)
        except Exception as e:
            logger.warning("[Scheduler] bad cron %r for task %r: %s", task.cron, task.name, e)
            return None
    if task.every_seconds > 0:
        return now + task.every_seconds
    return None


class Scheduler:
    def __init__(self, bridge, manager):
        self._bridge = bridge
        self._manager = manager
        self._loops: list[asyncio.Task] = []
        self._by_name: dict[str, TaskConfig] = {}
        self._stopping = False

    def start(self, tasks: list[TaskConfig]):
        """Spawn one loop per enabled task. Safe to call multiple times —
        any previous loops are cancelled first."""
        self.stop()
        self._stopping = False
        self._by_name = {}
        started = 0
        for t in tasks:
            self._by_name[t.name] = t
            if not t.enabled:
                continue
            if not t.prompt.strip():
                logger.warning("[Scheduler] Skipping task %r: empty prompt", t.name)
                continue
            if not t.cron and t.every_seconds <= 0:
                logger.warning("[Scheduler] Skipping task %r: no schedule", t.name)
                continue
            if next_fire_at(t) is None:
                logger.warning("[Scheduler] Skipping task %r: invalid schedule", t.name)
                continue
            self._loops.append(asyncio.create_task(self._run_loop(t)))
            started += 1
        if started:
            logger.info("[Scheduler] Started %d task(s)", started)

    def stop(self):
        self._stopping = True
        for t in self._loops:
            t.cancel()
        self._loops.clear()

    def reload(self):
        """Re-read tasks from config and restart all loops."""
        from . import config as cfg_mod
        cfg_mod.reload()
        self.start(cfg_mod.config.tasks)

    async def run_once(self, name: str) -> bool:
        """Fire `name` immediately (out-of-schedule). Returns True if dispatched."""
        task = self._by_name.get(name)
        if task is None:
            logger.warning("[Scheduler] run_once: unknown task %r", name)
            return False
        try:
            await self._fire(task)
            return True
        except Exception as e:
            logger.exception("[Scheduler] run_once(%s) failed: %s", name, e)
            return False

    async def _run_loop(self, task: TaskConfig):
        kind = f"cron={task.cron!r}" if task.cron else f"every {task.every_seconds}s"
        logger.info("[Scheduler] %s: %s → %s (ws=%s)",
                    task.name, kind, task.target_platform, task.workspace)
        try:
            while not self._stopping:
                fire_at = next_fire_at(task)
                if fire_at is None:
                    logger.warning("[Scheduler] %s: no next fire time, stopping", task.name)
                    return
                delay = max(1.0, fire_at - time.time())
                await asyncio.sleep(delay)
                if self._stopping:
                    return
                t0 = time.monotonic()
                try:
                    await self._fire(task)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.exception("[Scheduler] %s: fire failed: %s", task.name, e)
                logger.info("[Scheduler] %s: took %.1fs", task.name, time.monotonic() - t0)
        except asyncio.CancelledError:
            logger.info("[Scheduler] %s: cancelled", task.name)

    # ── Firing a single task ──

    async def _fire(self, task: TaskConfig):
        from .config import config
        if task.workspace not in config.workspaces:
            logger.warning("[Scheduler] %s: unknown workspace %r, skipping",
                           task.name, task.workspace)
            return

        targets = self._resolve_targets(task)
        if not targets:
            logger.warning("[Scheduler] %s: no valid targets, skipping", task.name)
            return

        # Dedicated chat_id keeps scheduler prompts out of any user's history
        # but still pins the session to the requested workspace.
        chat_id = f"scheduler.{task.workspace}.{task.name}"
        self._bridge.switch_workspace(chat_id, task.workspace)

        loop = asyncio.get_running_loop()
        # bridge.prompt is synchronous + blocking (acquires a lock); run in a
        # thread so the scheduler's asyncio loop stays responsive. The
        # per-workspace lock inside bridge.prompt() is what makes human chat
        # and scheduler runs queue instead of clashing.
        result = await loop.run_in_executor(
            None,
            lambda: self._bridge.prompt(chat_id, task.prompt, author=f"scheduler:{task.name}"),
        )
        text = (result.text or "").strip() if result else ""
        if not text:
            logger.warning("[Scheduler] %s: empty response, nothing to push", task.name)
            return

        await self._push(task.target_platform, targets, text)

    def _resolve_targets(self, task: TaskConfig) -> list:
        """Return the final list of target ids for this task, honoring the
        adapter's allowlist. Unknown ids are dropped with a warning.
        Webchat has no allowlist and uses `["*"]` as a broadcast sentinel."""
        from .config import config
        platform = task.target_platform

        if platform == "webchat":
            return ["*"]  # broadcast only — chat_ids are ignored

        section_map = {"telegram": config.telegram, "lark": config.lark, "discord": config.discord}
        sec = section_map.get(platform)
        if sec is None:
            logger.warning("[Scheduler] %s: unknown target_platform %r",
                           task.name, platform)
            return []

        allowed = list(sec.allowed_user_ids)
        if not task.target_chat_ids:
            # Broadcast: use whole allowlist.
            if not allowed:
                logger.warning("[Scheduler] %s: broadcast target but allowlist is empty",
                               task.name)
            return allowed

        # Explicit ids — must be in allowlist.
        allowed_set = {str(x) for x in allowed}
        resolved = []
        for cid in task.target_chat_ids:
            if str(cid) in allowed_set:
                resolved.append(cid)
            else:
                logger.warning("[Scheduler] %s: target %r not in %s allowlist — dropped",
                               task.name, cid, platform)
        return resolved

    async def _push(self, platform: str, targets: list, text: str):
        """Fan-out `text` to each target on `platform`, pausing between to
        stay under rate limits."""
        if platform == "webchat":
            await self._push_webchat(text)
            return

        adapter = self._manager._instances.get(platform)  # noqa: SLF001
        if adapter is None:
            logger.warning("[Scheduler] adapter %s is not running, cannot push", platform)
            return

        send = getattr(adapter, "send_text", None)
        if not send:
            logger.warning("[Scheduler] adapter %s has no send_text()", platform)
            return

        for i, cid in enumerate(targets):
            try:
                chat_id = self._chat_id_for(platform, cid)
                await send(chat_id, text)
            except Exception as e:
                logger.warning("[Scheduler] send_text(%s, %s) failed: %s",
                               platform, cid, e)
            if i + 1 < len(targets):
                await asyncio.sleep(BROADCAST_PAUSE)

    def _chat_id_for(self, platform: str, cid) -> str:
        from .adapters.base import make_chat_id
        channel = {"telegram": "tg", "lark": "lark", "discord": "discord"}[platform]
        return make_chat_id(channel, "direct", cid)

    async def _push_webchat(self, text: str):
        """Broadcast a message card into every open /chat tab."""
        web = self._manager._instances.get("web")  # may not exist
        if web is None:
            # WebAdapter isn't managed by the manager; reach it via the
            # global module-level instance the WebServer sets up.
            from . import server as _srv
            web = getattr(_srv, "_web_adapter_ref", None)
        if web is None or not getattr(web, "_clients", None):
            logger.info("[Scheduler] webchat broadcast: no open /chat tabs")
            return

        from nicegui import ui
        from .webui.chat import KIRO_AVATAR
        count = 0
        for client_id, state in list(web._clients.items()):  # noqa: SLF001
            container = state.get("container")
            if container is None:
                continue
            try:
                with container:
                    ui.chat_message(text=text, name="Kiro", sent=False, avatar=KIRO_AVATAR)
                web._append_history({  # noqa: SLF001
                    "role": "kiro",
                    "text": text,
                    "tool_calls": [],
                    "images": [],
                    "ts": time.time(),
                })
                count += 1
            except Exception as e:
                logger.debug("[Scheduler] webchat push to %s failed: %s", client_id, e)
        logger.info("[Scheduler] webchat broadcast delivered to %d tab(s)", count)
