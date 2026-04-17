"""Config page — tokens, ACP settings, workspace management."""

from pathlib import Path
from nicegui import ui

from ..manager import manager


def register():
    @ui.page("/config")
    def config_page():
        from ..config_manager import load_config_file, save_config_file

        ui.query("body").classes("bg-gray-50")
        with ui.column().classes("w-full max-w-6xl mx-auto py-6 px-4 gap-6"):
            with ui.row().classes("w-full items-center"):
                ui.label("Configuration").classes("text-2xl font-bold text-gray-700")
                ui.space()
                ui.link("← Back", "/").classes("text-blue-500")

            current = load_config_file()

            # ACP settings
            ui.label("ACP Settings").classes("text-lg font-semibold text-gray-600")
            with ui.card().classes("w-full"):
                cli_path = ui.input("kiro-cli Path", value=current.get("kiro_cli_path", "kiro-cli")).classes("w-full")
                ws_mode = ui.select(["per_chat", "fixed"], value=current.get("workspace_mode", "per_chat"),
                                    label="Workspace Mode").classes("w-40")
                ws_dir = ui.input("Working Dir", value=current.get("working_dir", "")).classes("w-full")
                idle = ui.number("Idle Timeout (s)", value=current.get("idle_timeout", 300),
                                 min=0, step=60).classes("w-40")
                prompt_to = ui.number("Response Timeout (s)", value=current.get("response_timeout", 3600),
                                      min=60, step=60).classes("w-40")

            # Adapter fields
            ui.label("Adapter Credential").classes("text-lg font-semibold text-gray-600")
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

            # Workspaces
            ui.label("Workspaces").classes("text-lg font-semibold text-gray-600")
            ws_data = current.get("_workspaces", {})
            if not ws_data:
                ws_data = {"default": {"path": str(Path.home() / ".local/share/kiro2chat/workspaces/default")}}
            ws_rows: list[dict] = []
            for n, v in ws_data.items():
                if isinstance(v, dict):
                    ws_rows.append({"name": n, "path": v.get("path", ""), "session_id": v.get("session_id", "")})
                else:
                    ws_rows.append({"name": n, "path": str(v), "session_id": ""})

            with ui.card().classes("w-full"):
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

                @ui.refreshable
                def _ws_rows_view():
                    for i, row in enumerate(ws_rows):
                        with ui.row().classes("w-full items-center gap-2"):
                            ui.input(value=row["name"],
                                     on_change=lambda e, idx=i: _update(idx, "name", e.value)).classes("w-32")
                            ui.input(value=row["path"],
                                     on_change=lambda e, idx=i: _update(idx, "path", e.value)).classes("flex-grow")
                            ui.input(value=row.get("session_id", ""), placeholder="session_id (可选)",
                                     on_change=lambda e, idx=i: _update(idx, "session_id", e.value)).classes("w-64")
                            ui.button(icon="delete",
                                      on_click=lambda idx=i: _del_ws(idx)).props("flat dense round color=red size=sm")

                _ws_rows_view()
                ui.button("+ Add Workspace", on_click=_add_ws).props("flat dense size=sm").classes("mt-1")

            def save():
                data = load_config_file()
                data["tg_bot_token"] = tg.value.strip()
                data["lark_app_id"] = lark_id.value.strip()
                data["lark_app_secret"] = lark_secret.value.strip()
                data["lark_domain"] = lark_dom.value
                data["discord_bot_token"] = discord.value.strip()
                data["kiro_cli_path"] = cli_path.value.strip()
                data["workspace_mode"] = ws_mode.value
                if ws_dir.value.strip():
                    data["working_dir"] = ws_dir.value.strip()
                data["idle_timeout"] = int(idle.value or 300)
                data["response_timeout"] = int(prompt_to.value or 3600)
                ws_out = {}
                latest_ws = load_config_file().get("_workspaces", {})
                for r in ws_rows:
                    if not r["name"] or not r["path"]:
                        continue
                    entry = {"path": r["path"]}
                    # Use UI value if set, otherwise preserve latest from config
                    sid = r.get("session_id") or ""
                    if not sid:
                        latest = latest_ws.get(r["name"])
                        if isinstance(latest, dict):
                            sid = latest.get("session_id", "")
                    if sid:
                        entry["session_id"] = sid
                    ws_out[r["name"]] = entry
                data["_workspaces"] = ws_out
                data = {k: v for k, v in data.items() if v != "" and v is not None}
                save_config_file(data)
                manager.refresh_config()
                ui.notify("Saved to config.toml", type="positive")

            ui.button("Save", on_click=save, color="primary").classes("mt-4")
