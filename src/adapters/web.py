"""Web adapter — Admin Dashboard + Chat UI via NiceGUI."""

import asyncio
import base64
import concurrent.futures
import logging
import threading
from pathlib import Path
from nicegui import ui, app

from ..acp.bridge import Bridge
from ..manager import manager

logger = logging.getLogger(__name__)


def _mask(val: str) -> str:
    """Mask a secret: show first 4 and last 4 chars."""
    if len(val) <= 10:
        return "***" if val else ""
    return f"{val[:4]}***{val[-4:]}"


# ── Dashboard Page ──

def _register_dashboard(bridge: Bridge):
    @ui.page("/")
    def dashboard():
        ui.query("body").classes("bg-gray-50")
        with ui.column().classes("w-full max-w-4xl mx-auto py-6 px-4 gap-6"):
            with ui.row().classes("items-center"):
                ui.label("Kiro2Chat").classes("text-2xl font-bold text-gray-700")
                ui.space()
                ui.link("💬 Chat", "/chat").classes("text-blue-500")
                ui.link("⚙️ Config", "/config").classes("text-blue-500")

            # Adapter status cards
            ui.label("Adapters").classes("text-lg font-semibold text-gray-600")
            cards = {}
            with ui.row().classes("gap-4 flex-wrap"):
                for name in ("telegram", "lark", "discord"):
                    with ui.card().classes("w-56") as card:
                        cards[name] = card
                        _adapter_card(name)

            # Sessions table
            ui.label("Active Sessions").classes("text-lg font-semibold text-gray-600 mt-4")
            session_container = ui.column().classes("w-full")
            _refresh_sessions(bridge, session_container)

            ui.timer(5.0, lambda: _refresh_sessions(bridge, session_container))

    def _adapter_card(name: str):
        states = manager.get_states()
        state = states.get(name, {"status": "unconfigured", "uptime": 0})
        status = state["status"]
        color = {"running": "green", "stopped": "orange", "unconfigured": "grey"}[status]
        icon = {"running": "check_circle", "stopped": "stop_circle", "unconfigured": "help"}[status]

        with ui.row().classes("items-center gap-2"):
            ui.icon(icon, color=color).classes("text-2xl")
            ui.label(name.capitalize()).classes("text-lg font-medium")
        ui.label(status).classes(f"text-{color}-600 text-sm")
        if status == "running":
            ui.label(f"uptime: {state['uptime']}s").classes("text-xs text-gray-400")

        with ui.row().classes("mt-2 gap-2"):
            if status == "stopped":
                ui.button("Start", on_click=lambda n=name: _start(n), color="green").props("dense size=sm")
            elif status == "running":
                ui.button("Stop", on_click=lambda n=name: _stop(n), color="red").props("dense size=sm")
            else:
                ui.button("Configure", on_click=lambda: ui.navigate.to("/config")).props("dense size=sm flat")

    def _start(name):
        try:
            manager.start_adapter(name)
            ui.notify(f"✅ {name} started", type="positive")
        except Exception as e:
            ui.notify(f"❌ {e}", type="negative")
        ui.navigate.to("/")

    def _stop(name):
        manager.stop_adapter(name)
        ui.notify(f"🛑 {name} stopped")
        ui.navigate.to("/")

    def _refresh_sessions(bridge, container):
        container.clear()
        sessions = bridge.get_sessions()
        if not sessions:
            with container:
                ui.label("No active sessions").classes("text-gray-400 text-sm")
            return
        with container:
            columns = [
                {"name": "chat_id", "label": "Chat ID", "field": "chat_id"},
                {"name": "session_id", "label": "Session ID", "field": "session_id"},
                {"name": "idle", "label": "Idle (s)", "field": "idle"},
            ]
            rows = [
                {"chat_id": cid, "session_id": info["session_id"][:12] + "...", "idle": info["idle_seconds"]}
                for cid, info in sessions.items()
            ]
            ui.table(columns=columns, rows=rows).classes("w-full")


# ── Config Page ──

def _register_config():
    @ui.page("/config")
    def config_page():
        from ..config_manager import load_config_file, save_config_file

        ui.query("body").classes("bg-gray-50")
        with ui.column().classes("w-full max-w-3xl mx-auto py-6 px-4 gap-6"):
            with ui.row().classes("items-center"):
                ui.label("Configuration").classes("text-2xl font-bold text-gray-700")
                ui.space()
                ui.link("← Dashboard", "/").classes("text-blue-500")

            current = load_config_file()

            # Token fields
            ui.label("Adapter Tokens").classes("text-lg font-semibold text-gray-600")
            with ui.card().classes("w-full"):
                tg = ui.input("Telegram Bot Token", value=current.get("tg_bot_token", ""),
                              placeholder="Enter token...").classes("w-full")
                with ui.row().classes("gap-4 w-full"):
                    lark_id = ui.input("Lark App ID", value=current.get("lark_app_id", "")).classes("flex-grow")
                    lark_secret = ui.input("Lark App Secret", value=current.get("lark_app_secret", "")).classes("flex-grow")
                lark_dom = ui.select(["feishu", "lark"], value=current.get("lark_domain", "feishu"),
                                     label="Lark Domain").classes("w-40")
                discord = ui.input("Discord Bot Token", value=current.get("discord_bot_token", ""),
                                   placeholder="Enter token...").classes("w-full")

            # ACP settings
            ui.label("ACP Settings").classes("text-lg font-semibold text-gray-600")
            with ui.card().classes("w-full"):
                cli_path = ui.input("kiro-cli Path", value=current.get("kiro_cli_path", "kiro-cli")).classes("w-full")
                ws_mode = ui.select(["per_chat", "fixed"], value=current.get("workspace_mode", "per_chat"),
                                    label="Workspace Mode").classes("w-40")
                ws_dir = ui.input("Working Dir", value=current.get("working_dir", "")).classes("w-full")
                idle = ui.number("Idle Timeout (s)", value=current.get("idle_timeout", 300),
                                 min=0, step=60).classes("w-40")

            # Workspaces
            ui.label("Workspaces").classes("text-lg font-semibold text-gray-600")
            ws_data = current.get("_workspaces", {"default": str(Path.home() / ".local/share/kiro2chat/workspaces/default")})
            ws_rows: list[dict] = []
            for n, v in ws_data.items():
                if isinstance(v, dict):
                    ws_rows.append({"name": n, "path": v.get("path", ""), "session_id": v.get("session_id", "")})
                else:
                    ws_rows.append({"name": n, "path": str(v), "session_id": ""})

            with ui.card().classes("w-full"):
                ws_container = ui.column().classes("w-full gap-2")

                def _render_ws():
                    ws_container.clear()
                    with ws_container:
                        for i, row in enumerate(ws_rows):
                            with ui.row().classes("w-full items-center gap-2"):
                                ui.input(value=row["name"], on_change=lambda e, idx=i: ws_rows.__setitem__(idx, {**ws_rows[idx], "name": e.value})).classes("w-32")
                                ui.input(value=row["path"], on_change=lambda e, idx=i: ws_rows.__setitem__(idx, {**ws_rows[idx], "path": e.value})).classes("flex-grow")
                                ui.input(value=row.get("session_id", ""), placeholder="session_id (可选)",
                                         on_change=lambda e, idx=i: ws_rows.__setitem__(idx, {**ws_rows[idx], "session_id": e.value})).classes("w-64")
                                ui.button(icon="delete", on_click=lambda idx=i: (_del_ws(idx))).props("flat dense round color=red size=sm")

                def _del_ws(idx):
                    if ws_rows[idx]["name"] == "default":
                        ui.notify("Cannot delete default workspace", type="warning")
                        return
                    ws_rows.pop(idx)
                    _render_ws()

                def _add_ws():
                    ws_rows.append({"name": "", "path": ""})
                    _render_ws()

                _render_ws()
                ui.button("+ Add Workspace", on_click=_add_ws).props("flat dense size=sm").classes("mt-1")

            def save():
                data = load_config_file()
                # Tokens
                data["tg_bot_token"] = tg.value.strip()
                data["lark_app_id"] = lark_id.value.strip()
                data["lark_app_secret"] = lark_secret.value.strip()
                data["lark_domain"] = lark_dom.value
                data["discord_bot_token"] = discord.value.strip()
                # ACP
                data["kiro_cli_path"] = cli_path.value.strip()
                data["workspace_mode"] = ws_mode.value
                if ws_dir.value.strip():
                    data["working_dir"] = ws_dir.value.strip()
                data["idle_timeout"] = int(idle.value or 300)
                # Workspaces
                ws_out = {}
                for r in ws_rows:
                    if not r["name"] or not r["path"]:
                        continue
                    if r.get("session_id"):
                        ws_out[r["name"]] = {"path": r["path"], "session_id": r["session_id"]}
                    else:
                        ws_out[r["name"]] = r["path"]
                data["_workspaces"] = ws_out
                # Remove empty keys
                data = {k: v for k, v in data.items() if v != "" and v is not None}
                save_config_file(data)
                manager.refresh_config()
                ui.notify("✅ Saved to config.toml", type="positive")

            ui.button("Save", on_click=save, color="primary").classes("mt-4")


# ── Chat Page (existing) ──

def _register_chat(adapter: "WebAdapter"):
    @ui.page("/chat")
    def chat_page():
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
                ui.link("Dashboard", "/").classes("text-blue-500 text-sm")
                ui.button(icon="delete", on_click=lambda: _clear(client_id, container)) \
                    .props("flat round color=grey")

            container = ui.column().classes("w-full flex-grow overflow-auto px-4 pb-4 items-stretch")

            pending_images: list[tuple[str, str]] = []
            preview_row = ui.row().classes("w-full px-4 gap-2 flex-wrap")

            async def on_upload(e):
                data = await e.file.read()
                b64 = base64.b64encode(data).decode()
                mime = e.file.content_type or "image/jpeg"
                pending_images.append((b64, mime))
                with preview_row:
                    idx = len(pending_images) - 1
                    with ui.row().classes("items-center gap-1 bg-gray-100 rounded px-2 py-1"):
                        ui.image(f"data:{mime};base64,{b64}").classes("w-12 h-12 object-cover rounded")
                        def remove(i=idx):
                            if i < len(pending_images):
                                pending_images.pop(i)
                            preview_row.clear()
                            _rebuild_previews()
                        ui.button(icon="close", on_click=remove).props("flat dense round size=xs")
                upload_el.reset()

            def _rebuild_previews():
                for i, (b64, mime) in enumerate(pending_images):
                    with preview_row:
                        with ui.row().classes("items-center gap-1 bg-gray-100 rounded px-2 py-1"):
                            ui.image(f"data:{mime};base64,{b64}").classes("w-12 h-12 object-cover rounded")
                            def remove(idx=i):
                                if idx < len(pending_images):
                                    pending_images.pop(idx)
                                preview_row.clear()
                                _rebuild_previews()
                            ui.button(icon="close", on_click=remove).props("flat dense round size=xs")

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
                preview_row.clear()
                await adapter._send(msg, container, client_id, images=imgs)

            send_btn.on_click(handle_send)
            text_input.on("keydown.enter", handle_send)

        def _clear(cid, cont):
            adapter._bridge.clear(adapter._chat_id(cid))
            cont.clear()
            ui.notify("🗑 会话已重置")


# ── WebAdapter ──

class WebAdapter:
    def __init__(self, bridge: Bridge, host: str = "127.0.0.1", port: int = 7860):
        self._bridge = bridge
        self._host = host
        self._port = port
        self._session_locks: dict[str, threading.Lock] = {}
        self._permission_futures: dict[str, concurrent.futures.Future] = {}

    def _chat_id(self, client_id: str) -> str:
        return f"web.private.{client_id}"

    def _handle_command(self, text: str, cid: str, container) -> bool:
        lower = text.strip().lower()

        from .base import dispatch_command
        result = dispatch_command(self._bridge, cid, text)
        if result:
            if lower == "/reset":
                container.clear()
                ui.notify(result)
            else:
                with container:
                    ui.chat_message(text=result, name="System", sent=False)
            return True

        return False

    async def _send(self, text: str, container, client_id: str, images=None):
        if not text.strip() and not images:
            return

        cid = self._chat_id(client_id)

        # Permission reply
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

        if self._handle_command(text, cid, container):
            return

        if cid not in self._session_locks:
            self._session_locks[cid] = threading.Lock()
        lock = self._session_locks[cid]
        if lock.locked():
            ui.notify("上一条消息还在处理中，请稍候...", type="warning")
            return

        with container:
            with ui.chat_message(name="You", sent=True):
                if text:
                    ui.label(text)
                if images:
                    with ui.row().classes("gap-2 flex-wrap"):
                        for b64, mime in images:
                            ui.image(f"data:{mime};base64,{b64}").classes("w-32 rounded")
            with ui.chat_message(name="Kiro", sent=False):
                response_label = ui.html("⏳ Thinking...")

        loop = asyncio.get_running_loop()
        accumulated = ""

        def do_prompt():
            nonlocal accumulated
            def on_stream(_chunk, acc):
                nonlocal accumulated
                accumulated = acc
            with lock:
                return self._bridge.prompt(cid, text, images=images, timeout=300, on_stream=on_stream)

        future = loop.run_in_executor(None, do_prompt)

        while not future.done():
            await asyncio.sleep(0.3)
            if accumulated:
                response_label.content = _escape(accumulated)

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
            response_label.content = _escape(final)

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
                                    ui.image(f"data:{mime};base64,{data}").classes("w-64 rounded")
                                except Exception:
                                    ui.label(f"📷 {path}")
        except Exception as e:
            response_label.content = f"❌ {_escape(str(e))}"

        ui.run_javascript("window.scrollTo(0, document.body.scrollHeight)")

    def _handle_permission(self, chat_id, request):
        fut = concurrent.futures.Future()
        self._permission_futures[chat_id] = fut
        logger.info("[Web] Permission requested for %s: %s", chat_id, request.description)
        try:
            return fut.result(timeout=120)
        except concurrent.futures.TimeoutError:
            self._permission_futures.pop(chat_id, None)
            return "deny"

    def start(self):
        self._bridge.on_permission_request("web.", self._handle_permission)
        manager.init(self._bridge)

        _register_dashboard(self._bridge)
        _register_config()
        _register_chat(self)

        ui.run(
            host=self._host,
            port=self._port,
            title="Kiro2Chat",
            storage_secret="kiro2chat-web",
            show=False,
            reload=False,
        )


def _escape(text: str) -> str:
    return (text
            .replace("&", "&amp;")
            .replace('"', "&quot;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\n", "<br>"))
