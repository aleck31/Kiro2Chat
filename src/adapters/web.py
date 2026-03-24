"""Web chat adapter using NiceGUI."""

import asyncio
import concurrent.futures
import logging
import threading
from nicegui import ui, app

from ..acp.bridge import Bridge
from ..acp.client import PermissionRequest

logger = logging.getLogger(__name__)


class WebAdapter:
    def __init__(self, bridge: Bridge, host: str = "127.0.0.1", port: int = 8080):
        self._bridge = bridge
        self._host = host
        self._port = port
        self._session_locks: dict[str, threading.Lock] = {}
        self._permission_futures: dict[str, concurrent.futures.Future] = {}
        self._permission_containers: dict[str, object] = {}

    def _chat_id(self, client_id: str) -> str:
        return f"web.private.{client_id}"

    def _handle_command(self, text: str, cid: str, container) -> bool:
        """Handle slash commands. Returns True if handled."""
        lower = text.strip().lower()

        if lower in ("/cancel", "cancel"):
            self._bridge.cancel(cid)
            with container:
                ui.chat_message(text="🛑 Cancelled", name="System", sent=False)
            return True

        if lower in ("/clear", "clear"):
            self._bridge._sessions.pop(cid, None)
            container.clear()
            ui.notify("会话已清除")
            return True

        if lower.startswith("/model"):
            parts = text.split(maxsplit=1)
            arg = parts[1].strip() if len(parts) > 1 else ""
            if not arg:
                models = self._bridge.get_available_models(cid)
                current = self._bridge.get_current_model(cid)
                if models:
                    lines = []
                    for m in models:
                        mid = m.get("modelId", m) if isinstance(m, dict) else str(m)
                        marker = " ✓" if mid == current else ""
                        lines.append(f"• {mid}{marker}")
                    body = "\n".join(lines)
                else:
                    body = "(先发一条消息开始会话)"
                msg = f"当前: {current or 'unknown'}\n\n{body}\n\n切换: /model <name>"
            else:
                try:
                    self._bridge.set_model(cid, arg)
                    msg = f"✅ Model: {arg}"
                except Exception as e:
                    msg = f"❌ {e}"
            with container:
                ui.chat_message(text=msg, name="System", sent=False)
            return True

        if lower.startswith("/agent"):
            parts = text.split(maxsplit=1)
            arg = parts[1].strip() if len(parts) > 1 else ""
            if not arg:
                modes = self._bridge.get_available_modes(cid)
                current = self._bridge.get_current_mode(cid)
                if modes:
                    lines = []
                    for m in modes:
                        mid = m.get("id", m) if isinstance(m, dict) else str(m)
                        marker = " ✓" if mid == current else ""
                        lines.append(f"• {mid}{marker}")
                    body = "\n".join(lines)
                else:
                    body = "(先发一条消息开始会话)"
                msg = f"当前: {current or 'unknown'}\n\n{body}\n\n切换: /agent <name>"
            else:
                try:
                    self._bridge.set_mode(cid, arg)
                    msg = f"✅ Agent: {arg}"
                except Exception as e:
                    msg = f"❌ {e}"
            with container:
                ui.chat_message(text=msg, name="System", sent=False)
            return True

        if lower in ("/help", "help"):
            with container:
                ui.chat_message(
                    text="/model — 查看/切换模型\n/agent — 查看/切换 Agent\n/cancel — 取消当前操作\n/clear — 重置会话",
                    name="System", sent=False,
                )
            return True

        return False

    async def _send(self, text: str, container, client_id: str):
        """Send user message and get AI response with streaming."""
        if not text.strip():
            return

        cid = self._chat_id(client_id)

        # Check permission reply
        if cid in self._permission_futures:
            lower = text.strip().lower()
            if lower in ("y", "yes", "ok", "n", "no", "t", "trust", "always"):
                fut = self._permission_futures.pop(cid, None)
                if fut and not fut.done():
                    if lower in ("y", "yes", "ok"):
                        fut.set_result("allow_once")
                    elif lower in ("t", "trust", "always"):
                        fut.set_result("allow_always")
                    else:
                        fut.set_result("deny")
                return

        # Commands
        if self._handle_command(text, cid, container):
            return

        # Concurrency lock per session
        if cid not in self._session_locks:
            self._session_locks[cid] = threading.Lock()
        lock = self._session_locks[cid]
        if lock.locked():
            ui.notify("上一条消息还在处理中，请稍候...", type="warning")
            return

        # User message
        with container:
            ui.chat_message(text=text, name="You", sent=True)
            response_msg = ui.chat_message(name="Kiro", sent=False)
            spinner = ui.spinner("dots")

        loop = asyncio.get_running_loop()
        accumulated = ""

        def do_prompt():
            nonlocal accumulated

            def on_stream(_chunk: str, acc: str):
                nonlocal accumulated
                accumulated = acc

            with lock:
                return self._bridge.prompt(cid, text, timeout=300, on_stream=on_stream)

        future = loop.run_in_executor(None, do_prompt)

        while not future.done():
            await asyncio.sleep(0.3)
            if accumulated:
                response_msg.props(f'text-html="{_escape(accumulated)}"')

        try:
            result = future.result()
            parts = []
            if result.tool_calls:
                for tc in result.tool_calls:
                    icon = {"completed": "✅", "failed": "❌"}.get(tc.status, "🔧")
                    parts.append(f"{icon} {tc.title}")
                parts.append("")
            if result.text:
                parts.append(result.text)
            final = "\n".join(parts) or accumulated or "(no response)"
            response_msg.props(remove='text-html')
            response_msg.props(f'text-html="{_escape(final)}"')
        except Exception as e:
            response_msg.props(remove='text-html')
            response_msg.props(f'text-html="❌ {_escape(str(e))}"')
        finally:
            container.remove(spinner)

        ui.run_javascript("window.scrollTo(0, document.body.scrollHeight)")

    def start(self):
        self._bridge.on_permission_request(self._handle_permission)

        @ui.page("/")
        def index():
            client_id = app.storage.browser.get("client_id")
            if not client_id:
                import uuid
                client_id = str(uuid.uuid4())[:8]
                app.storage.browser["client_id"] = client_id

            ui.query("body").classes("bg-gray-50")

            with ui.column().classes("w-full max-w-3xl mx-auto h-screen"):
                with ui.row().classes("w-full items-center py-3 px-4"):
                    ui.label("Kiro Chat").classes("text-xl font-bold text-gray-700")
                    ui.space()
                    ui.button(icon="delete", on_click=lambda: _clear(client_id, container)) \
                        .props("flat round color=grey")

                container = ui.column().classes("w-full flex-grow overflow-auto px-4 pb-4")

                with ui.row().classes("w-full px-4 pb-4 items-center"):
                    text_input = ui.input(placeholder="输入消息... (/help 查看命令)") \
                        .props("rounded outlined dense input-class=mx-2") \
                        .classes("flex-grow")
                    send_btn = ui.button(icon="send").props("round color=primary")

                async def handle_send():
                    msg = text_input.value
                    text_input.value = ""
                    await self._send(msg, container, client_id)

                send_btn.on_click(handle_send)
                text_input.on("keydown.enter", handle_send)

            def _clear(cid, cont):
                self._bridge._sessions.pop(self._chat_id(cid), None)
                cont.clear()
                ui.notify("会话已清除")

        ui.run(
            host=self._host,
            port=self._port,
            title="Kiro Chat",
            storage_secret="kiro2chat-web",
            show=False,
            reload=False,
        )

    def _handle_permission(self, chat_id: str, request: PermissionRequest) -> str | None:
        """Handle permission request — block until user responds via chat."""
        if not chat_id.startswith("web."):
            return None

        fut = concurrent.futures.Future()
        self._permission_futures[chat_id] = fut

        # We can't directly manipulate UI from bridge thread,
        # so permission prompt is shown when user next interacts.
        # For now, log it — user sees the prompt in chat flow.
        logger.info("[Web] Permission requested for %s: %s", chat_id, request.description)

        try:
            return fut.result(timeout=120)
        except concurrent.futures.TimeoutError:
            self._permission_futures.pop(chat_id, None)
            return "deny"


def _escape(text: str) -> str:
    """Escape text for HTML attribute embedding."""
    return (text
            .replace("&", "&amp;")
            .replace('"', "&quot;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\n", "<br>"))
