"""Dashboard page — adapter status, sessions, controls."""

from datetime import datetime

from nicegui import ui

from ..acp.bridge import Bridge
from ..manager import manager
from .layout import page_shell


# ── Status display config ──

_STATUS_STYLE = {
    "running":      {"color": "green",  "icon": "play_circle",  "label": "Running"},
    "stopped":      {"color": "grey",   "icon": "stop_circle",  "label": "Stopped"},
    "disabled":     {"color": "slate",  "icon": "block",        "label": "Disabled"},
    "unconfigured": {"color": "orange", "icon": "help",         "label": "Not configured"},
    "builtin":      {"color": "blue",   "icon": "hub",          "label": "Built-in"},
}

_ADAPTER_META = {
    "web":      {"label": "Web Chat", "icon": "chat"},
    "telegram": {"label": "Telegram", "icon": "send"},
    "lark":     {"label": "Lark",     "icon": "business"},
    "discord":  {"label": "Discord",  "icon": "forum"},
}


def _fmt_uptime(sec: int) -> str:
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m {sec % 60}s"
    return f"{sec // 3600}h {(sec % 3600) // 60}m"


def _fmt_idle(sec: int) -> str:
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m"
    return f"{sec // 3600}h"


def _ellipsis(s: str, max_len: int = 24) -> str:
    return s if len(s) <= max_len else s[: max_len - 1] + "…"


def register(bridge: Bridge):
    @ui.page("/")
    def dashboard():
        with page_shell(current="dashboard"):
            _overview_bar(bridge)

            ui.label("Adapters").classes("text-lg font-semibold text-gray-700")
            _adapters_view()

            ui.label("Active Sessions").classes("text-lg font-semibold text-gray-700 mt-2")
            _sessions_view(bridge)

            # Adapters + overview show per-second uptime → 1s tick.
            # Sessions change slowly and idle column uses coarse units → 3s tick.
            def _tick_fast():
                _overview_bar.refresh()
                _adapters_view.refresh()
            ui.timer(1.0, _tick_fast)
            ui.timer(3.0, _sessions_view.refresh)

    # ── Overview bar ──

    @ui.refreshable
    def _overview_bar(bridge: Bridge):
        states = manager.get_states()
        # Web Chat is always-on (built-in); count it in both numerator and denominator.
        running = 1 + sum(1 for s in states.values() if s["status"] == "running")
        enabled = 1 + sum(1 for s in states.values() if s.get("enabled"))
        sessions = bridge.get_sessions()

        with ui.row().classes("w-full gap-3"):
            _stat_card("bolt", "Adapters", f"{running} / {enabled}", "running / enabled")
            _stat_card("forum", "Sessions", str(len(sessions)), "active ACP sessions")
            _stat_card("schedule", "Idle Timeout", f"{_fmt_uptime(_config_idle())}", "before session reap")

    # ── Adapter cards ──

    @ui.refreshable
    def _adapters_view():
        states = manager.get_states()
        with ui.row().classes("w-full gap-3 flex-wrap"):
            _adapter_card("web", {"status": "builtin", "uptime": 0})
            for name in ("telegram", "lark", "discord"):
                _adapter_card(name, states.get(name, {"status": "unconfigured", "uptime": 0}))

    def _adapter_card(name: str, state: dict):
        meta = _ADAPTER_META[name]
        status = state["status"]
        style = _STATUS_STYLE[status]

        with ui.card().classes("flex-1 min-w-[240px] min-h-[160px] p-5 gap-3"):
            # Header: platform icon + name + status pill
            with ui.row().classes("items-center gap-3 w-full"):
                ui.icon(meta["icon"]).classes("text-2xl text-gray-600")
                ui.label(meta["label"]).classes("text-base font-semibold text-gray-800")
                ui.space()
                with ui.row().classes(f"items-center gap-1 text-{style['color']}-600"):
                    ui.icon(style["icon"]).classes("text-base")
                    ui.label(style["label"]).classes("text-xs font-medium")

            # Status hint line
            if status == "running" and state.get("uptime", 0) > 0:
                with ui.row().classes("items-center gap-1 text-xs text-gray-500"):
                    ui.icon("schedule").classes("text-sm")
                    ui.label(f"up {_fmt_uptime(state['uptime'])}")
            elif status == "builtin":
                ui.label("Always on while daemon runs").classes("text-xs text-gray-500")
            elif status == "unconfigured":
                ui.label("Missing credentials").classes("text-xs text-gray-400")
            elif status == "disabled":
                ui.label("Disabled in settings").classes("text-xs text-gray-400")
            else:
                ui.label("\u00A0").classes("text-xs")  # non-breaking space for height alignment

            # Actions
            with ui.row().classes("gap-2 mt-auto"):
                if name == "web":
                    ui.button("Open Chat", icon="open_in_new",
                              on_click=lambda: ui.navigate.to("/chat")) \
                        .props('dense size=sm padding="xs sm" color=primary outline')
                elif status == "stopped":
                    ui.button("Start", icon="play_arrow",
                              on_click=lambda n=name: _start(n)) \
                        .props('dense size=sm padding="xs sm" color=positive')
                elif status == "running":
                    ui.button("Stop", icon="stop",
                              on_click=lambda n=name: _stop(n)) \
                        .props('dense size=sm padding="xs sm" color=negative outline')
                else:
                    # disabled | unconfigured → unified Configure action
                    ui.button("Configure", icon="settings",
                              on_click=lambda: ui.navigate.to("/settings?tab=adapters")) \
                        .props('dense size=sm padding="xs sm" flat')

    def _start(name):
        try:
            manager.start_adapter(name)
            ui.notify(f"{name} started", type="positive")
        except Exception as e:
            ui.notify(str(e), type="negative")
        _adapters_view.refresh()
        _overview_bar.refresh(manager.bridge) if manager.bridge else None

    def _stop(name):
        manager.stop_adapter(name)
        ui.notify(f"{name} stopped", type="warning")
        _adapters_view.refresh()
        _overview_bar.refresh(manager.bridge) if manager.bridge else None

    # ── Sessions table ──

    @ui.refreshable
    def _sessions_view(bridge: Bridge):
        sessions = bridge.get_sessions()
        if not sessions:
            with ui.card().classes("w-full p-6"):
                with ui.column().classes("items-center w-full gap-2 text-gray-400"):
                    ui.icon("inbox").classes("text-4xl")
                    ui.label("No active sessions").classes("text-sm")
                    ui.label("Sessions appear here when users send messages.") \
                        .classes("text-xs")
            return

        with ui.card().classes("w-full p-0"):
            with ui.element("div").classes("w-full overflow-x-auto"):
                # Header row
                with ui.row().classes(
                    "w-full items-center px-4 py-2 text-xs font-medium text-gray-500 "
                    "border-b border-gray-200 bg-gray-50 gap-0"
                ):
                    ui.label("Workspace").classes("w-40")
                    ui.label("Chat IDs").classes("flex-grow")
                    ui.label("Session").classes("w-40")
                    ui.label("Started").classes("w-20")
                    ui.label("Idle").classes("w-16 text-right")
                    ui.label("").classes("w-10")

                # Data rows
                for s in sessions:
                    _session_row(bridge, s)

    def _session_row(bridge: Bridge, s: dict):
        sid_full = s["session_id"]
        sid_short = sid_full[:8] + "…" if len(sid_full) > 8 else sid_full
        chat_ids = s["chat_id"]
        started = datetime.fromtimestamp(s["started_at"]).strftime("%H:%M:%S")
        idle = _fmt_idle(s["idle_seconds"])

        with ui.row().classes(
            "w-full items-center px-4 py-2 text-sm text-gray-700 "
            "border-b border-gray-100 hover:bg-gray-50 gap-0"
        ):
            with ui.row().classes("w-40 items-center gap-1"):
                ui.icon("folder").classes("text-base text-gray-400")
                ui.label(s["workspace"]).classes("font-medium")

            ui.label(_ellipsis(chat_ids, 40)).classes("flex-grow text-gray-600") \
                .tooltip(chat_ids)

            ui.label(sid_short).classes("w-40 font-mono text-xs text-gray-500") \
                .tooltip(sid_full)

            ui.label(started).classes("w-20 text-xs text-gray-500")
            ui.label(idle).classes("w-16 text-xs text-gray-500 text-right")

            with ui.row().classes("w-10 justify-end"):
                ui.button(icon="delete_outline",
                          on_click=lambda ws=s["workspace"], cid=chat_ids.split(",")[0].strip():
                              _clear_session(bridge, ws, cid)) \
                    .props("flat dense round size=sm color=grey") \
                    .tooltip("Reset this workspace session")

    def _clear_session(bridge: Bridge, workspace: str, chat_id: str):
        if not chat_id or chat_id == "(none)":
            return
        try:
            bridge.clear(chat_id)
            ui.notify(f"Session cleared for workspace '{workspace}'", type="positive")
        except Exception as e:
            ui.notify(f"Failed to clear: {e}", type="negative")
        _sessions_view.refresh(bridge)


# ── Small helpers ──

def _stat_card(icon: str, label: str, value: str, hint: str):
    with ui.card().classes("flex-1 min-w-[180px] p-4"):
        with ui.row().classes("items-center gap-2 w-full"):
            ui.icon(icon, color="primary").classes("text-xl")
            ui.label(label).classes("text-xs text-gray-500 font-medium uppercase tracking-wide")
        ui.label(value).classes("text-2xl font-bold text-gray-800 mt-1")
        ui.label(hint).classes("text-xs text-gray-400")


def _config_idle() -> int:
    from ..config import config
    return int(getattr(config, "idle_timeout", 300) or 300)
