"""Settings page — tabbed config (ACP / Adapters / Workspaces), per-tab save."""

from pathlib import Path
from nicegui import ui

from ..manager import manager
from .layout import page_shell


TABS = [
    ("acp",        "ACP",        "settings_applications"),
    ("workspaces", "Workspaces", "folder"),
    ("adapters",   "Adapters",   "vpn_key"),
]


def register():
    @ui.page("/settings")
    def settings_page(tab: str = "acp"):
        from ..config_manager import load_config_file

        with page_shell(current="settings"):
            current = load_config_file()

            initial = tab if tab in {key for key, _, _ in TABS} else "acp"

            with ui.card().classes("w-full p-0 overflow-hidden"):
                with ui.tabs().props("align=left dense no-caps").classes("w-full bg-gray-50") as tabs:
                    tab_refs = {
                        key: ui.tab(name=key, label=label, icon=icon)
                        for key, label, icon in TABS
                    }

                with ui.tab_panels(tabs, value=tab_refs[initial]).classes("w-full"):
                    with ui.tab_panel(tab_refs["acp"]).classes("p-6"):
                        _panel_acp(current)
                    with ui.tab_panel(tab_refs["workspaces"]).classes("p-6"):
                        _panel_workspaces(current)
                    with ui.tab_panel(tab_refs["adapters"]).classes("p-6"):
                        _panel_adapters(current)


# ── Panel: ACP ──

def _panel_acp(current: dict):
    from ..config_manager import load_config_file

    import shutil
    raw_cli = current.get("kiro_cli_path", "kiro-cli")
    resolved = shutil.which(raw_cli) or "(not found in PATH)"
    cli_path = ui.input(
        "kiro-cli Path",
        value=raw_cli,
        placeholder="kiro-cli (name) or /absolute/path",
    ).classes("w-full").tooltip(
        f"Bare name resolves via $PATH → {resolved}\n"
        f"Write an absolute path only if you need a specific binary."
    )
    with ui.row().classes("items-center gap-1 text-xs text-gray-500 -mt-1"):
        ui.icon("link").classes("text-sm")
        ui.label(f"Resolves to: {resolved}").classes("font-mono")

    # Build workspace options from [workspaces] section
    ws_data = current.get("_workspaces", {})
    ws_names = list(ws_data.keys()) if ws_data else ["default"]
    fixed_ws_value = current.get("fixed_workspace") or ("default" if "default" in ws_names else ws_names[0])

    idle = ui.number(
        "Idle Timeout (s)",
        value=current.get("idle_timeout", 300),
        min=0, step=60,
    ).classes("w-full mt-2").props(
        'hint="Reap idle ACP sessions after N seconds (0 to disable)"'
    )
    prompt_to = ui.number(
        "Response Timeout (s)",
        value=current.get("response_timeout", 3600),
        min=60, step=60,
    ).classes("w-full mt-2").props(
        'hint="Max wait for a single kiro-cli response"'
    )

    with ui.row().classes("w-full gap-4 mt-2 items-end"):
        ws_mode = ui.select(
            ["per_chat", "fixed"],
            value=current.get("workspace_mode", "per_chat"),
            label="Workspace Mode",
        ).classes("w-48")
        fixed_ws = ui.select(
            ws_names, value=fixed_ws_value,
            label="Fixed Workspace",
        ).classes("flex-grow").tooltip(
            "In fixed mode, all chats share this workspace regardless of /workspace command. "
            "In per_chat mode this is ignored."
        )
        fixed_ws.bind_enabled_from(ws_mode, "value", backward=lambda v: v == "fixed")

    with ui.row().classes("items-center gap-1 mt-2 text-amber-600"):
        ui.icon("warning_amber").classes("text-base")
        ui.label("ACP changes require a daemon restart to take effect.") \
            .classes("text-xs")

    def save():
        data = load_config_file()
        data["kiro_cli_path"] = cli_path.value.strip() or "kiro-cli"
        data["workspace_mode"] = ws_mode.value
        data["fixed_workspace"] = fixed_ws.value or "default"
        data.pop("working_dir", None)  # remove deprecated key if present
        data["idle_timeout"] = int(idle.value or 300)
        data["response_timeout"] = int(prompt_to.value or 3600)
        _write(data)
        ui.notify("ACP settings saved (restart daemon to apply)", type="positive")

    _save_button(save)


# ── Panel: Adapters ──

def _panel_adapters(current: dict):
    from ..config_manager import load_config_file

    # ── Telegram ──
    with _adapter_group("Telegram", "send", current.get("tg_enabled", True)) as grp:
        tg = ui.input(
            "Bot Token",
            value=current.get("tg_bot_token", ""),
            placeholder="123456:ABC-DEF...",
            password=True, password_toggle_button=True,
        ).classes("w-full")
        tg.bind_enabled_from(grp.enabled, "value")
        tg_enabled = grp.enabled

    # ── Lark / Feishu ──
    with _adapter_group("Lark / Feishu", "business", current.get("lark_enabled", True)) as grp:
        with ui.row().classes("w-full gap-4"):
            lark_id = ui.input(
                "App ID",
                value=current.get("lark_app_id", ""),
            ).classes("flex-grow")
            lark_secret = ui.input(
                "App Secret",
                value=current.get("lark_app_secret", ""),
                password=True, password_toggle_button=True,
            ).classes("flex-grow")
        lark_dom = ui.select(
            ["feishu", "lark"],
            value=current.get("lark_domain", "feishu"),
            label="Domain",
        ).classes("w-48 mt-2")
        for el in (lark_id, lark_secret, lark_dom):
            el.bind_enabled_from(grp.enabled, "value")
        lark_enabled = grp.enabled

    # ── Discord ──
    with _adapter_group("Discord", "forum", current.get("discord_enabled", True)) as grp:
        discord = ui.input(
            "Bot Token",
            value=current.get("discord_bot_token", ""),
            placeholder="MTIzNDU2Nzg5...",
            password=True, password_toggle_button=True,
        ).classes("w-full")
        discord.bind_enabled_from(grp.enabled, "value")
        discord_enabled = grp.enabled

    with ui.row().classes("items-center gap-1 mt-2 text-gray-500"):
        ui.icon("info").classes("text-base")
        ui.label("Disabling an adapter stops it immediately if running.") \
            .classes("text-xs")

    def save():
        data = load_config_file()
        data["tg_bot_token"] = tg.value.strip()
        data["tg_enabled"] = bool(tg_enabled.value)
        data["lark_app_id"] = lark_id.value.strip()
        data["lark_app_secret"] = lark_secret.value.strip()
        data["lark_domain"] = lark_dom.value
        data["lark_enabled"] = bool(lark_enabled.value)
        data["discord_bot_token"] = discord.value.strip()
        data["discord_enabled"] = bool(discord_enabled.value)
        _write(data)

        # Stop any adapter that is now disabled.
        states = manager.get_states()
        stopped = []
        for adapter_name, enabled_now in (
            ("telegram", tg_enabled.value),
            ("lark",     lark_enabled.value),
            ("discord",  discord_enabled.value),
        ):
            if not enabled_now and states.get(adapter_name, {}).get("status") == "running":
                manager.stop_adapter(adapter_name)
                stopped.append(adapter_name)

        manager.refresh_config()

        if stopped:
            ui.notify(f"Saved. Stopped: {', '.join(stopped)}", type="positive")
        else:
            ui.notify("Adapter settings saved", type="positive")

    _save_button(save)


# ── Panel: Workspaces ──

def _panel_workspaces(current: dict):
    from ..config_manager import load_config_file

    ws_rows: list[dict] = []
    ws_data = current.get("_workspaces", {})
    if not ws_data:
        ws_data = {"default": {"path": str(Path.home() / ".local/share/kiro2chat/workspaces/default")}}
    for n, v in ws_data.items():
        if isinstance(v, dict):
            ws_rows.append({"name": n, "path": v.get("path", ""), "session_id": v.get("session_id", "")})
        else:
            ws_rows.append({"name": n, "path": str(v), "session_id": ""})

    def _update(idx: int, field: str, value: str):
        ws_rows[idx] = {**ws_rows[idx], field: value}

    def _del_ws(idx: int):
        if ws_rows[idx]["name"] == "default":
            ui.notify("Cannot delete default workspace", type="warning")
            return
        ws_rows.pop(idx)
        _rows_view.refresh()

    def _add_ws():
        ws_rows.append({"name": "", "path": "", "session_id": ""})
        _rows_view.refresh()

    # Column headers
    with ui.row().classes("w-full text-xs text-gray-500 px-1 mb-1"):
        ui.label("Name").classes("w-32")
        ui.label("Path").classes("flex-grow")
        ui.label("Session ID (optional)").classes("w-80")
        ui.element("div").classes("w-8")

    @ui.refreshable
    def _rows_view():
        for i, row in enumerate(ws_rows):
            with ui.row().classes("w-full items-center gap-2"):
                ui.input(
                    value=row["name"], placeholder="name",
                    on_change=lambda e, idx=i: _update(idx, "name", e.value),
                ).classes("w-32").props("dense outlined")
                ui.input(
                    value=row["path"], placeholder="/absolute/path",
                    on_change=lambda e, idx=i: _update(idx, "path", e.value),
                ).classes("flex-grow").props("dense outlined")
                ui.input(
                    value=row.get("session_id", ""), placeholder="(auto)",
                    on_change=lambda e, idx=i: _update(idx, "session_id", e.value),
                ).classes("w-80").props("dense outlined")
                ui.button(
                    icon="delete_outline",
                    on_click=lambda idx=i: _del_ws(idx),
                ).props("flat dense round color=grey size=sm") \
                 .tooltip("Delete workspace")

    _rows_view()
    ui.button("Add Workspace", icon="add", on_click=_add_ws) \
        .props("flat dense size=sm color=primary").classes("mt-2")

    def save():
        data = load_config_file()
        ws_out = {}
        latest_ws = data.get("_workspaces", {})
        for r in ws_rows:
            if not r["name"] or not r["path"]:
                continue
            entry = {"path": r["path"]}
            sid = r.get("session_id") or ""
            if not sid:
                latest = latest_ws.get(r["name"])
                if isinstance(latest, dict):
                    sid = latest.get("session_id", "")
            if sid:
                entry["session_id"] = sid
            ws_out[r["name"]] = entry
        data["_workspaces"] = ws_out
        _write(data)
        ui.notify("Workspaces saved", type="positive")

    _save_button(save)


# ── Helpers ──

def _save_button(handler):
    with ui.row().classes("w-full justify-end mt-6"):
        ui.button("Save", icon="save", on_click=handler, color="primary") \
            .props("dense")


class _AdapterGroup:
    """Group a platform's credential fields with an Enable switch at the top-right."""

    def __init__(self, card, body, enabled_switch):
        self._card = card
        self._body = body
        self.enabled = enabled_switch

    def __enter__(self):
        self._card.__enter__()
        self._body.__enter__()
        return self

    def __exit__(self, *args):
        self._body.__exit__(*args)
        return self._card.__exit__(*args)


def _adapter_group(title: str, icon: str, initial_enabled: bool):
    card = ui.card().classes("w-full p-4 mb-3")
    with card:
        with ui.row().classes("w-full items-center gap-2"):
            ui.icon(icon, color="primary").classes("text-xl")
            ui.label(title).classes("text-base font-semibold text-gray-700")
            ui.space()
            enabled = ui.switch("Enabled", value=bool(initial_enabled)) \
                .props("dense color=positive")
        ui.separator().classes("my-2")
        body = ui.column().classes("w-full gap-2")
    return _AdapterGroup(card, body, enabled)


def _write(data: dict):
    """Strip empty values before saving."""
    from ..config_manager import save_config_file
    clean = {k: v for k, v in data.items() if v != "" and v is not None}
    save_config_file(clean)
