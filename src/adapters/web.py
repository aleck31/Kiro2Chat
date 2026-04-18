"""Web chat adapter using NiceGUI.

Per-user state:
- messages are persisted to `app.storage.user["messages"]` (last 200) so the
  history survives tab close/reopen and is shared across tabs of the same
  browser session. Cleared by `/reset` and by the reset button.
- container / loop references are cached in `self._clients[client_id]`
  so the sync permission handler can reach the right browser tab.

Permission UX:
- request is rendered as an inline card inside the chat flow (mobile-friendly)
- three buttons (Allow / Trust / Deny) resolve a concurrent.futures.Future
- card self-deletes 1 s after a decision, matching Telegram's behavior
- typed y / n / t in the input still works as a fallback
"""

import asyncio
import base64
import concurrent.futures
import logging
import threading
import time

from nicegui import ui, app

from ..acp.bridge import Bridge
from ..manager import manager

logger = logging.getLogger(__name__)

MAX_HISTORY = 200
PERMISSION_TIMEOUT = 120  # seconds; matches previous behavior


class WebAdapter:
    def __init__(self, bridge: Bridge, host: str = "127.0.0.1", port: int = 7860):
        self._bridge = bridge
        self._host = host
        self._port = port
        self._session_locks: dict[str, threading.Lock] = {}
        self._permission_queues: dict[str, list[concurrent.futures.Future]] = {}
        # client_id → {container, loop}; set when a tab opens /chat
        self._clients: dict[str, dict] = {}
        self._main_loop: asyncio.AbstractEventLoop | None = None

    def _chat_id(self, client_id: str) -> str:
        from .base import make_chat_id
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

    def _reset_history(self, container, client_id_or_cid: str):
        """Clear container + storage and re-render welcome placeholder."""
        container.clear()
        app.storage.user["messages"] = []
        # Update any client state bound to this chat (may not match exactly,
        # so we refresh welcome for every state that uses this container)
        with container:
            welcome = _welcome_placeholder()
        for state in self._clients.values():
            if state.get("container") is container:
                state["welcome"] = welcome

    async def _send(self, text: str, container, client_id: str, images=None):
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
            _render_user_message(text, images)
        self._append_history({
            "role": "user",
            "text": text,
            "images": images or [],
            "ts": time.time(),
        })

        with container:
            with ui.chat_message(name="Kiro", sent=False):
                response_label = ui.html(_THINKING_HTML)

        # User just sent a message — always jump to bottom.
        _scroll_to_bottom(force=True)

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
                response_label.content = _escape(accumulated)
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
            response_label.content = _escape(final)

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
                                    _clickable_image(f"data:{mime};base64,{data}", "w-64 rounded")
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
                f'<span class="align-middle">{_escape(str(e))}</span>'
            )
            self._append_history({
                "role": "kiro",
                "text": f"[error] {e}",
                "ts": time.time(),
            })

        _scroll_to_bottom()

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
        _scroll_to_bottom(force=True)

    # ── Page registration ──

    def _register_chat(self):
        adapter = self

        @ui.page("/chat")
        def chat_page():
            from ..webui.layout import page_shell
            client_id = app.storage.browser.get("client_id")
            if not client_id:
                import uuid
                client_id = str(uuid.uuid4())[:8]
                app.storage.browser["client_id"] = client_id

            with page_shell(current="chat"):
                ui.add_head_html(_IMAGE_PREVIEW_HTML)

                with ui.row().classes("w-full items-center"):
                    ui.label("Chat").classes("text-lg font-semibold text-gray-600")
                    ui.space()
                    ui.button(
                        icon="restart_alt",
                        on_click=lambda: _reset_chat(client_id, container),
                    ).props("flat round color=grey").tooltip("Reset session")

                container = ui.column().classes(
                    "w-full flex-grow overflow-auto items-stretch"
                ).style("min-height: 60vh")

                # Register this tab for permission-card delivery
                client_state: dict = {"container": container, "welcome": None}
                adapter._clients[client_id] = client_state

                # Render prior history or welcome placeholder
                history = adapter._load_history()
                if history:
                    with container:
                        for entry in history:
                            _render_history_entry(entry)
                    # Page just opened — jump straight to latest message.
                    _scroll_to_bottom(force=True)
                else:
                    with container:
                        client_state["welcome"] = _welcome_placeholder()

                pending_images: list[tuple[str, str]] = []
                preview_row = ui.row().classes("w-full px-4 gap-2 flex-wrap")

                @ui.refreshable
                def _preview_view():
                    for i, (b64, mime) in enumerate(pending_images):
                        with ui.row().classes("items-center gap-1 bg-gray-100 rounded px-2 py-1"):
                            ui.image(f"data:{mime};base64,{b64}").classes("w-12 h-12 object-cover rounded")
                            def remove(idx=i):
                                if idx < len(pending_images):
                                    pending_images.pop(idx)
                                _preview_view.refresh()
                            ui.button(icon="close", on_click=remove) \
                                .props("flat dense round size=xs")

                with preview_row:
                    _preview_view()

                async def on_upload(e):
                    data = await e.file.read()
                    b64 = base64.b64encode(data).decode()
                    mime = e.file.content_type or "image/jpeg"
                    pending_images.append((b64, mime))
                    _preview_view.refresh()
                    upload_el.reset()

                with ui.row().classes("w-full px-4 pb-4 items-center"):
                    text_input = ui.input(placeholder="输入消息... (/help 查看命令)") \
                        .props("rounded outlined dense input-class=mx-2") \
                        .classes("flex-grow")
                    upload_el = ui.upload(on_upload=on_upload, auto_upload=True) \
                        .props('accept="image/*" flat dense max-files=1 hide-upload-btn') \
                        .style("display: none")
                    ui.button(icon="image", on_click=lambda: upload_el.run_method('pickFiles')) \
                        .props("flat round color=grey")
                    send_btn = ui.button(icon="send").props("round color=primary")

                async def handle_send():
                    msg = text_input.value
                    text_input.value = ""
                    imgs = pending_images.copy() or None
                    pending_images.clear()
                    _preview_view.refresh()
                    await adapter._send(msg, container, client_id, images=imgs)

                send_btn.on_click(handle_send)
                text_input.on("keydown.enter", handle_send)

            def _reset_chat(cid, cont):
                adapter._bridge.clear(adapter._chat_id(cid))
                adapter._reset_history(cont, cid)
                ui.notify("已重置会话")

    def start(self):
        self._bridge.on_permission_request("web.", self._handle_permission)
        manager.init(self._bridge)

        async def _on_startup():
            self._main_loop = asyncio.get_running_loop()
            manager._auto_start()

        app.on_startup(_on_startup)

        from ..webui import register_pages
        register_pages(self._bridge)
        self._register_chat()

        ui.run(
            host=self._host,
            port=self._port,
            title="Kiro2Chat",
            storage_secret="kiro2chat-web",
            show=False,
            reload=False,
        )


# ── Rendering helpers ──

def _clickable_image(data_url: str, thumb_classes: str):
    """Render a thumbnail that opens a shared full-size preview dialog on click."""
    img = ui.image(data_url).classes(thumb_classes + " cursor-pointer hover:opacity-90")
    # Escape both backslashes and single quotes for the JS literal
    safe = data_url.replace("\\", "\\\\").replace("'", "\\'")
    img.on("click", lambda _=None, s=safe: ui.run_javascript(f"window.__k2c_openPreview('{s}')"))


def _render_user_message(text: str, images):
    with ui.chat_message(name="You", sent=True):
        if text:
            ui.label(text)
        if images:
            with ui.row().classes("gap-2 flex-wrap"):
                for b64, mime in images:
                    _clickable_image(f"data:{mime};base64,{b64}", "w-32 rounded")


def _render_history_entry(entry: dict):
    role = entry.get("role")
    text = entry.get("text", "")
    images = entry.get("images") or []

    if role == "user":
        _render_user_message(text, images)
    elif role == "system":
        ui.chat_message(text=text, name="System", sent=False)
    else:  # kiro
        with ui.chat_message(name="Kiro", sent=False):
            parts = []
            for tc in entry.get("tool_calls") or []:
                icon = {"completed": "✅", "failed": "❌"}.get(tc.get("status"), "🔧")
                parts.append(f"{icon} {tc.get('title', '')}")
            if parts:
                parts.append("")
            if text:
                parts.append(text)
            ui.html(_escape("\n".join(parts)) or "(empty)")
            if images:
                with ui.row().classes("gap-2 flex-wrap"):
                    for b64, mime in images:
                        _clickable_image(f"data:{mime};base64,{b64}", "w-64 rounded")


def _welcome_placeholder():
    """Render + return the welcome column so the caller can delete it later."""
    col = ui.column().classes("w-full items-center gap-2 py-10 text-gray-400")
    with col:
        ui.icon("forum").classes("text-5xl")
        ui.label("Start a conversation").classes("text-base font-medium")
        ui.label("Type a message below. /help lists available commands.") \
            .classes("text-xs")
    return col


def _scroll_to_bottom(force: bool = False):
    """Scroll window to bottom only when the user is already at the bottom,
    so new messages don't interrupt someone scrolling through history.
    `force=True` overrides this (e.g. first render of history on page load).
    """
    try:
        if force:
            ui.run_javascript("window.scrollTo(0, document.body.scrollHeight)")
        else:
            ui.run_javascript("""
                (function() {
                  var threshold = 120;  // px — treat "near bottom" as bottom
                  var pos = window.innerHeight + window.scrollY;
                  if (document.body.scrollHeight - pos < threshold) {
                    window.scrollTo(0, document.body.scrollHeight);
                  }
                })();
            """)
    except Exception:
        pass


_THINKING_HTML = (
    '<span class="material-icons text-gray-400 align-middle" '
    'style="animation: spin 1.2s linear infinite;">autorenew</span> '
    '<span class="text-gray-500 align-middle">Thinking...</span>'
    '<style>@keyframes spin { to { transform: rotate(360deg); } }</style>'
)


# Full-screen image preview — injected once per page; clicking a thumbnail
# fires window.__k2c_openPreview(<data-url>) which reuses this overlay.
_IMAGE_PREVIEW_HTML = """
<style>
#k2c-image-preview {
  position: fixed; inset: 0;
  background: rgba(0,0,0,0.8);
  display: none;
  align-items: center; justify-content: center;
  z-index: 9999;
  cursor: zoom-out;
}
#k2c-image-preview img {
  max-width: 95vw; max-height: 90vh;
  border-radius: 6px;
  box-shadow: 0 20px 60px rgba(0,0,0,0.4);
}
</style>
<div id="k2c-image-preview" onclick="this.style.display='none'"><img id="k2c-image-preview-img"></div>
<script>
window.__k2c_openPreview = function(src) {
  var box = document.getElementById('k2c-image-preview');
  var img = document.getElementById('k2c-image-preview-img');
  img.src = src;
  box.style.display = 'flex';
};
document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape') {
    var box = document.getElementById('k2c-image-preview');
    if (box) box.style.display = 'none';
  }
});
</script>
"""


def _escape(text: str) -> str:
    return (text
            .replace("&", "&amp;")
            .replace('"', "&quot;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\n", "<br>"))


def _mask(val: str) -> str:
    if len(val) <= 10:
        return "***" if val else ""
    return f"{val[:4]}***{val[-4:]}"
