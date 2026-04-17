"""Dashboard page — adapter status, sessions, controls."""

from nicegui import ui

from ..acp.bridge import Bridge
from ..manager import manager
from .layout import page_shell


def register(bridge: Bridge):
    @ui.page("/")
    def dashboard():
        with page_shell(current="dashboard"):
            ui.label("Adapters").classes("text-lg font-semibold text-gray-600")
            with ui.row().classes("w-full gap-4"):
                with ui.card().classes("flex-1"):
                    with ui.row().classes("items-center gap-2"):
                        ui.icon("check_circle", color="green").classes("text-2xl")
                        ui.label("Web Chat").classes("text-lg font-medium")
                    ui.label("running").classes("text-green-600 text-sm")
                    with ui.row().classes("mt-2"):
                        ui.button("Open Chat", on_click=lambda: ui.navigate.to("/chat"), color="primary").props("dense size=sm")
                for name in ("telegram", "lark", "discord"):
                    with ui.card().classes("flex-1"):
                        _adapter_card(name)

            ui.label("Active Sessions").classes("text-lg font-semibold text-gray-600 mt-4")
            _sessions_view(bridge)
            ui.timer(5.0, _sessions_view.refresh)

    def _adapter_card(name: str):
        states = manager.get_states()
        state = states.get(name, {"status": "unconfigured", "uptime": 0})
        status = state["status"]
        color = {"running": "green", "stopped": "orange", "unconfigured": "grey"}[status]
        icon = {"running": "check_circle", "stopped": "stop_circle", "unconfigured": "help"}[status]

        with ui.row().classes("items-center gap-2"):
            ui.icon(icon, color=color).classes("text-2xl")
            ui.label(name.capitalize()).classes("text-lg font-medium")
        label = f"{status} (uptime: {state['uptime']}s)" if status == "running" else status
        ui.label(label).classes(f"text-{color}-600 text-sm")

        with ui.row().classes("mt-2 gap-2"):
            if status == "stopped":
                ui.button("Start", on_click=lambda n=name: _start(n), color="green").props("dense size=sm")
            elif status == "running":
                ui.button("Stop", on_click=lambda n=name: _stop(n), color="red").props("dense size=sm")
            else:
                ui.button("Configure", on_click=lambda: ui.navigate.to("/settings")).props("dense size=sm flat")

    def _start(name):
        try:
            manager.start_adapter(name)
            ui.notify(f"{name} started", type="positive")
        except Exception as e:
            ui.notify(str(e), type="negative")
        ui.navigate.to("/")

    def _stop(name):
        manager.stop_adapter(name)
        ui.notify(f"{name} stopped", type="warning")
        ui.navigate.to("/")

    @ui.refreshable
    def _sessions_view(bridge):
        sessions = bridge.get_sessions()
        if not sessions:
            ui.label("No active sessions").classes("text-gray-400 text-sm")
            return
        columns = [
            {"name": "chat_id", "label": "Chat ID", "field": "chat_id", "align": "left"},
            {"name": "workspace", "label": "Workspace", "field": "workspace", "align": "left"},
            {"name": "session_id", "label": "Session ID", "field": "session_id", "align": "left"},
            {"name": "started", "label": "Started", "field": "started", "align": "left"},
            {"name": "idle", "label": "Idle (s)", "field": "idle", "align": "right"},
        ]
        from datetime import datetime
        rows = [
            {
                "chat_id": s["chat_id"],
                "workspace": s["workspace"],
                "session_id": s["session_id"][:12] + "...",
                "started": datetime.fromtimestamp(s["started_at"]).strftime("%H:%M:%S"),
                "idle": s["idle_seconds"],
            }
            for s in sessions
        ]
        ui.table(columns=columns, rows=rows).classes("w-full")
