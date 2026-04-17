"""Settings page — sectioned config (ACP / Adapters / Workspaces), per-section save."""

from pathlib import Path
from nicegui import ui

from ..manager import manager
from .layout import page_shell


def register():
    @ui.page("/settings")
    def settings_page():
        from ..config_manager import load_config_file

        with page_shell(current="settings"):
            current = load_config_file()

            # ── ACP Settings ──
            with _section("ACP Settings", "settings_applications") as body:
                with body:
                    cli_path = ui.input(
                        "kiro-cli Path",
                        value=current.get("kiro_cli_path", "kiro-cli"),
                    ).classes("w-full")
                    with ui.row().classes("w-full gap-4"):
                        ws_mode = ui.select(
                            ["per_chat", "fixed"],
                            value=current.get("workspace_mode", "per_chat"),
                            label="Workspace Mode",
                        ).classes("w-48")
                        idle = ui.number(
                            "Idle Timeout (s)",
                            value=current.get("idle_timeout", 300),
                            min=0, step=60,
                        ).classes("w-48")
                        prompt_to = ui.number(
                            "Response Timeout (s)",
                            value=current.get("response_timeout", 3600),
                            min=60, step=60,
                        ).classes("w-48")
                    ws_dir = ui.input(
                        "Working Dir",
                        value=current.get("working_dir", ""),
                        placeholder="(default: /tmp/kiro2chat-workspaces)",
                    ).classes("w-full")

                    ui.label("⚠️ ACP changes require a daemon restart to take effect.") \
                        .classes("text-xs text-amber-600 mt-1")

                def save_acp():
                    data = load_config_file()
                    data["kiro_cli_path"] = cli_path.value.strip() or "kiro-cli"
                    data["workspace_mode"] = ws_mode.value
                    if ws_dir.value.strip():
                        data["working_dir"] = ws_dir.value.strip()
                    else:
                        data.pop("working_dir", None)
                    data["idle_timeout"] = int(idle.value or 300)
                    data["response_timeout"] = int(prompt_to.value or 3600)
                    _write(data)
                    ui.notify("ACP settings saved (restart daemon to apply)", type="positive")

                _save_button(save_acp)

            # ── Adapter Credentials ──
            with _section("Adapter Credentials", "vpn_key") as body:
                with body:
                    tg = ui.input(
                        "Telegram Bot Token",
                        value=current.get("tg_bot_token", ""),
                        placeholder="123456:ABC-DEF...",
                        password=True, password_toggle_button=True,
                    ).classes("w-full")
                    with ui.row().classes("w-full gap-4"):
                        lark_id = ui.input(
                            "Lark App ID",
                            value=current.get("lark_app_id", ""),
                        ).classes("flex-grow")
                        lark_secret = ui.input(
                            "Lark App Secret",
                            value=current.get("lark_app_secret", ""),
                            password=True, password_toggle_button=True,
                        ).classes("flex-grow")
                    lark_dom = ui.select(
                        ["feishu", "lark"],
                        value=current.get("lark_domain", "feishu"),
                        label="Lark Domain",
                    ).classes("w-48")
                    discord = ui.input(
                        "Discord Bot Token",
                        value=current.get("discord_bot_token", ""),
                        placeholder="MTIzNDU2Nzg5...",
                        password=True, password_toggle_button=True,
                    ).classes("w-full")

                def save_adapters():
                    data = load_config_file()
                    data["tg_bot_token"] = tg.value.strip()
                    data["lark_app_id"] = lark_id.value.strip()
                    data["lark_app_secret"] = lark_secret.value.strip()
                    data["lark_domain"] = lark_dom.value
                    data["discord_bot_token"] = discord.value.strip()
                    _write(data)
                    manager.refresh_config()
                    ui.notify("Adapter credentials saved", type="positive")

                _save_button(save_adapters)

            # ── Workspaces ──
            ws_rows: list[dict] = []
            ws_data = current.get("_workspaces", {})
            if not ws_data:
                ws_data = {"default": {"path": str(Path.home() / ".local/share/kiro2chat/workspaces/default")}}
            for n, v in ws_data.items():
                if isinstance(v, dict):
                    ws_rows.append({"name": n, "path": v.get("path", ""), "session_id": v.get("session_id", "")})
                else:
                    ws_rows.append({"name": n, "path": str(v), "session_id": ""})

            with _section("Workspaces", "folder") as body:
                def _update(idx: int, field: str, value: str):
                    ws_rows[idx] = {**ws_rows[idx], field: value}

                def _del_ws(idx: int):
                    if ws_rows[idx]["name"] == "default":
                        ui.notify("Cannot delete default workspace", type="warning")
                        return
                    ws_rows.pop(idx)
                    _ws_rows_view.refresh()

                def _add_ws():
                    ws_rows.append({"name": "", "path": "", "session_id": ""})
                    _ws_rows_view.refresh()

                with body:
                    with ui.row().classes("w-full text-xs text-gray-500 px-1"):
                        ui.label("Name").classes("w-32")
                        ui.label("Path").classes("flex-grow")
                        ui.label("Session ID (optional)").classes("w-64")
                        ui.element("div").classes("w-8")

                    @ui.refreshable
                    def _ws_rows_view():
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
                                    icon="delete",
                                    on_click=lambda idx=i: _del_ws(idx),
                                ).props("flat dense round color=red size=sm")

                    _ws_rows_view()
                    ui.button("Add Workspace", icon="add", on_click=_add_ws) \
                        .props("flat dense size=sm color=primary").classes("mt-1")

                def save_workspaces():
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

                _save_button(save_workspaces)


# ── helpers ──

def _section(title: str, icon: str):
    """Return a card container yielded for body. Caller appends Save button after body."""
    card = ui.card().classes("w-full")
    with card:
        with ui.row().classes("w-full items-center gap-2"):
            ui.icon(icon, color="primary").classes("text-xl")
            ui.label(title).classes("text-lg font-semibold text-gray-700")
        ui.separator().classes("my-1")
        body = ui.column().classes("w-full gap-3")
    return _SectionCtx(card, body)


class _SectionCtx:
    def __init__(self, card, body):
        self.card = card
        self.body = body

    def __enter__(self):
        self.card.__enter__()
        return self.body

    def __exit__(self, *args):
        return self.card.__exit__(*args)


def _save_button(handler):
    with ui.row().classes("w-full justify-end mt-2"):
        ui.button("Save", icon="save", on_click=handler, color="primary") \
            .props("dense")


def _write(data: dict):
    """Strip empty values before saving."""
    from ..config_manager import save_config_file
    clean = {k: v for k, v in data.items() if v != "" and v is not None}
    save_config_file(clean)
