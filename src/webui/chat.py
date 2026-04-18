"""Web chat page — renders /chat and wires the input box to WebAdapter._send().

This module is the NiceGUI page layer for chat. The adapter
(`src/adapters/web.py`) stays protocol-focused (send/receive/permission);
this module owns page layout, rendering helpers, and the welcome placeholder.
"""

import base64
import uuid

from nicegui import ui, app

from ..acp.bridge import Bridge


def register(bridge: Bridge, adapter):
    """Mount the /chat page, bound to the given WebAdapter instance."""

    @ui.page("/chat")
    def chat_page():
        from .layout import page_shell
        client_id = app.storage.browser.get("client_id")
        if not client_id:
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
                scroll_to_bottom(force=True)
            else:
                with container:
                    client_state["welcome"] = welcome_placeholder()

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
            bridge.clear(adapter._chat_id(cid))
            adapter._reset_history(cont, cid)
            ui.notify("已重置会话")


# ── Rendering helpers ──

def clickable_image(data_url: str, thumb_classes: str):
    """Render a thumbnail that opens a shared full-size preview dialog on click."""
    img = ui.image(data_url).classes(thumb_classes + " cursor-pointer hover:opacity-90")
    safe = data_url.replace("\\", "\\\\").replace("'", "\\'")
    img.on("click", lambda _=None, s=safe: ui.run_javascript(f"window.__k2c_openPreview('{s}')"))


def render_user_message(text: str, images):
    with ui.chat_message(name="You", sent=True):
        if text:
            ui.label(text)
        if images:
            with ui.row().classes("gap-2 flex-wrap"):
                for b64, mime in images:
                    clickable_image(f"data:{mime};base64,{b64}", "w-32 rounded")


def _render_history_entry(entry: dict):
    role = entry.get("role")
    text = entry.get("text", "")
    images = entry.get("images") or []

    if role == "user":
        render_user_message(text, images)
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
            ui.html(escape("\n".join(parts)) or "(empty)")
            if images:
                with ui.row().classes("gap-2 flex-wrap"):
                    for b64, mime in images:
                        clickable_image(f"data:{mime};base64,{b64}", "w-64 rounded")


def welcome_placeholder():
    """Render + return the welcome column so the caller can delete it later."""
    col = ui.column().classes("w-full items-center gap-2 py-10 text-gray-400")
    with col:
        ui.icon("forum").classes("text-5xl")
        ui.label("Start a conversation").classes("text-base font-medium")
        ui.label("Type a message below. /help lists available commands.") \
            .classes("text-xs")
    return col


def scroll_to_bottom(force: bool = False):
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


def escape(text: str) -> str:
    return (text
            .replace("&", "&amp;")
            .replace('"', "&quot;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\n", "<br>"))


THINKING_HTML = (
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
