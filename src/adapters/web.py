"""Web chat adapter — handles send/receive, commands, and permission requests.

The NiceGUI page layer lives in `src/webui/chat.py`; this module is
protocol-only, symmetric with the Telegram/Lark/Discord adapters.

Per-user state:
- messages are persisted to `app.storage.user["messages"]` (last 200) so the
  history survives tab close/reopen and is shared across tabs of the same
  browser session. Cleared by `/reset` and by the reset button.
- container / welcome references are cached in `self._clients[client_id]`
  so the sync permission handler can reach the right browser tab.
"""

import asyncio
import base64
import concurrent.futures
import logging
import threading
import time

from nicegui import ui, app

from .base import BaseAdapter, make_chat_id
from ..acp.bridge import Bridge

logger = logging.getLogger(__name__)

MAX_HISTORY = 200
PERMISSION_TIMEOUT = 120  # seconds


class WebAdapter(BaseAdapter):
    def __init__(self, bridge: Bridge):
        self._bridge = bridge
        self._session_locks: dict[str, threading.Lock] = {}
        self._permission_queues: dict[str, list[concurrent.futures.Future]] = {}
        # client_id → {container, welcome}; set when a tab opens /chat
        self._clients: dict[str, dict] = {}
        self._main_loop: asyncio.AbstractEventLoop | None = None

    # ── Lifecycle ──

    def bind_loop(self, loop: asyncio.AbstractEventLoop):
        """Called by WebServer once NiceGUI's event loop is running."""
        self._main_loop = loop

    async def start(self):
        self._bridge.on_permission_request("web.", self._handle_permission)

    async def stop(self):
        self._bridge.off_permission_request("web.")
        self._clients.clear()

    def _chat_id(self, client_id: str) -> str:
        return make_chat_id("web", "direct", client_id)

    # ── History persistence ──

    def _load_history(self) -> list[dict]:
        # app.storage.user is writable in async/event contexts and persists
        # across tab close/reopen. (app.storage.browser is read-only after
        # the initial page response, so it can't be used here.)
        return list(app.storage.user.get("messages", []))

    def _save_history(self, msgs: list[dict]):
        app.storage.user["messages"] = msgs[-MAX_HISTORY:]

    def _append_history(self, entry: dict):
        msgs = self._load_history()
        msgs.append(entry)
        self._save_history(msgs)

    def _reset_history(self, container, client_id_or_cid: str):
        """Clear container + storage and re-render welcome placeholder."""
        from ..webui.chat import welcome_placeholder
        container.clear()
        app.storage.user["messages"] = []
        with container:
            welcome = welcome_placeholder()
        for state in self._clients.values():
            if state.get("container") is container:
                state["welcome"] = welcome

    # ── Command / text input routing ──

    def _handle_command(self, text: str, cid: str, container) -> bool:
        lower = text.strip().lower()
        from .base import dispatch_command
        result = dispatch_command(self._bridge, cid, text)
        if result:
            if lower == "/reset":
                self._reset_history(container, cid)
                ui.notify(result)
            else:
                with container:
                    ui.chat_message(text=result, name="System", sent=False)
                self._append_history({"role": "system", "text": result, "ts": time.time()})
            return True
        return False

    async def _send(self, text: str, container, client_id: str, images=None):
        from ..webui.chat import (
            render_user_message, clickable_image, scroll_to_bottom,
            escape, THINKING_HTML,
        )
        if not text.strip() and not images:
            return

        cid = self._chat_id(client_id)
        text_stripped = text.strip()

        # Permission reply via text (kept as fallback for inline-card users)
        queue = self._permission_queues.get(cid, [])
        if queue:
            lower = text_stripped.lower()
            if lower in ("y", "yes", "ok", "n", "no", "t", "trust", "always"):
                fut = queue.pop(0)
                if not queue:
                    self._permission_queues.pop(cid, None)
                if fut and not fut.done():
                    decision = (
                        "allow_once" if lower in ("y", "yes", "ok")
                        else "allow_always" if lower in ("t", "trust", "always")
                        else "deny"
                    )
                    fut.set_result(decision)
                return
            # Non y/n/t while permissions pending → auto-deny all, then continue
            for fut in queue:
                if not fut.done():
                    fut.set_result("deny")
            self._permission_queues.pop(cid, None)

        if self._handle_command(text, cid, container):
            return

        if cid not in self._session_locks:
            self._session_locks[cid] = threading.Lock()
        lock = self._session_locks[cid]
        if lock.locked():
            ui.notify("上一条消息还在处理中，请稍候...", type="warning")
            return

        # First real message → drop the welcome placeholder
        client_state = self._clients.get(client_id) or {}
        welcome = client_state.get("welcome")
        if welcome is not None:
            try:
                welcome.delete()
            except Exception:
                pass
            client_state["welcome"] = None

        # Render user message + persist
        with container:
            render_user_message(text, images)
        self._append_history({
            "role": "user",
            "text": text,
            "images": images or [],
            "ts": time.time(),
        })

        with container:
            with ui.chat_message(name="Kiro", sent=False):
                response_label = ui.html(THINKING_HTML)

        # User just sent a message — always jump to bottom.
        scroll_to_bottom(force=True)

        loop = asyncio.get_running_loop()
        accumulated = ""

        def do_prompt():
            nonlocal accumulated
            def on_stream(_chunk, acc):
                nonlocal accumulated
                accumulated = acc
            with lock:
                return self._bridge.prompt(
                    cid, text, images=images, on_stream=on_stream, author=client_id,
                )

        future = loop.run_in_executor(None, do_prompt)

        def _tick():
            if accumulated and not future.done():
                response_label.content = escape(accumulated)
        stream_timer = ui.timer(0.3, _tick)
        try:
            await asyncio.wrap_future(future)
        finally:
            stream_timer.deactivate()

        try:
            result = future.result()
            parts = []
            tool_call_entries = []
            if result.tool_calls:
                for tc in result.tool_calls:
                    icon = {"completed": "✅", "failed": "❌"}.get(tc.status, "🔧")
                    line = f"{icon} {tc.title}"
                    parts.append(line)
                    tool_call_entries.append({"title": tc.title, "status": tc.status})
                parts.append("")
            if result.text:
                parts.append(result.text)
            final = "\n".join(parts) or accumulated or "(no response)"
            response_label.content = escape(final)

            output_images = []
            if result.image_paths:
                with container:
                    with ui.chat_message(name="Kiro", sent=False):
                        with ui.row().classes("gap-2 flex-wrap"):
                            for path in result.image_paths:
                                try:
                                    with open(path, "rb") as f:
                                        data = base64.b64encode(f.read()).decode()
                                    from ..acp.client import ACPClient
                                    mime = ACPClient._detect_image_mime(data) or "image/png"
                                    clickable_image(f"data:{mime};base64,{data}", "w-64 rounded")
                                    output_images.append((data, mime))
                                except Exception:
                                    with ui.row().classes("items-center gap-1 text-gray-500"):
                                        ui.icon("image").classes("text-base")
                                        ui.label(path)

            self._append_history({
                "role": "kiro",
                "text": result.text or "",
                "tool_calls": tool_call_entries,
                "images": output_images,
                "ts": time.time(),
            })

        except Exception as e:
            response_label.content = (
                '<span class="material-icons text-red-500 align-middle">error</span> '
                f'<span class="align-middle">{escape(str(e))}</span>'
            )
            self._append_history({
                "role": "kiro",
                "text": f"[error] {e}",
                "ts": time.time(),
            })

        scroll_to_bottom()

    # ── Permission handling ──

    def _handle_permission(self, chat_id, request):
        """Bridge calls us from a worker thread. We schedule an inline card on
        the page's event loop and block on the Future until the user clicks.
        """
        fut: concurrent.futures.Future = concurrent.futures.Future()
        self._permission_queues.setdefault(chat_id, []).append(fut)
        logger.info("[Web] Permission requested for %s: %s", chat_id, request.title)

        # Find the client tab bound to this chat_id (web.direct.<client_id>)
        client_id = chat_id.split(".", 2)[-1] if "." in chat_id else chat_id
        client = self._clients.get(client_id)

        if client and self._main_loop:
            try:
                asyncio.run_coroutine_threadsafe(
                    self._render_permission_card(client, fut, request),
                    self._main_loop,
                )
            except Exception as e:
                logger.warning("[Web] Failed to render permission card: %s", e)

        try:
            return fut.result(timeout=PERMISSION_TIMEOUT)
        except concurrent.futures.TimeoutError:
            queue = self._permission_queues.get(chat_id, [])
            if fut in queue:
                queue.remove(fut)
            if not queue:
                self._permission_queues.pop(chat_id, None)
            return "deny"

    async def _render_permission_card(self, client: dict, fut: concurrent.futures.Future, request):
        from ..webui.chat import scroll_to_bottom
        container = client["container"]
        with container:
            card = ui.card().classes(
                "w-full border-l-4 border-amber-500 bg-amber-50 p-4 my-1"
            )
            with card:
                with ui.row().classes("items-center gap-2 w-full"):
                    ui.icon("lock", color="amber").classes("text-xl")
                    ui.label("Kiro requests permission").classes("text-sm font-semibold text-gray-800")
                ui.label(request.title).classes("text-sm text-gray-700 mt-1")

                result_label = ui.label().classes("text-xs mt-1")
                result_label.set_visibility(False)

                btn_row = ui.row().classes("gap-2 mt-3")

                def decide(decision: str, label_text: str, color: str):
                    if not fut.done():
                        fut.set_result(decision)
                    btn_row.set_visibility(False)
                    result_label.text = label_text
                    result_label.classes(replace=f"text-xs mt-1 {color}")
                    result_label.set_visibility(True)

                    async def _remove_later():
                        await asyncio.sleep(1.0)
                        try:
                            card.delete()
                        except Exception:
                            pass
                    asyncio.create_task(_remove_later())

                with btn_row:
                    ui.button(
                        "Allow", icon="check",
                        on_click=lambda: decide("allow_once", "✅ Allowed", "text-green-600"),
                    ).props('dense size=sm color=positive')
                    ui.button(
                        "Trust", icon="verified",
                        on_click=lambda: decide("allow_always", "🔓 Trusted", "text-blue-600"),
                    ).props('dense size=sm color=primary outline')
                    ui.button(
                        "Deny", icon="block",
                        on_click=lambda: decide("deny", "🚫 Denied", "text-red-600"),
                    ).props('dense size=sm color=negative outline')
        # Permission cards demand attention — always surface them.
        scroll_to_bottom(force=True)
