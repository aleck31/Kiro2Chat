"""Dashboard page — adapter status, sessions, controls."""

from nicegui import ui

from ..acp.bridge import Bridge
from ..manager import manager


def register(bridge: Bridge):
    @ui.page("/")
    def dashboard():
        ui.query("body").classes("bg-gray-50")
        with ui.column().classes("w-full max-w-4xl mx-auto py-6 px-4 gap-6"):
            with ui.row().classes("items-center"):
                ui.label("Kiro2Chat").classes("text-2xl font-bold text-gray-700")
                ui.space()
                ui.link("💬 Chat", "/chat").classes("text-blue-500")
                ui.link("⚙️ Config", "/config").classes("text-blue-500")

            ui.label("Adapters").classes("text-lg font-semibold text-gray-600")
            with ui.row().classes("gap-4 flex-wrap"):
                for name in ("telegram", "lark", "discord"):
                    with ui.card().classes("w-56"):
                        _adapter_card(name)

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
