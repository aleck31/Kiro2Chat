"""Settings page — tabbed config (ACP / Adapters / Workspaces), per-tab save."""

from pathlib import Path
from nicegui import ui

from ..manager import manager
from .layout import page_shell


TABS = [
    ("acp",        "ACP",        "settings_applications"),
    ("workspaces", "Workspaces", "folder"),
    ("adapters",   "Adapters",   "vpn_key"),
    ("heartbeat",  "Heartbeat",  "schedule"),
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
                    with ui.tab_panel(tab_refs["heartbeat"]).classes("p-6"):
                        _panel_heartbeat(current)


# ── Panel: ACP ──

def _g(current: dict, section: str, key: str, default=None):
    """Read nested config: current[section][key] with fallback."""
    sec = current.get(section) or {}
    v = sec.get(key)
    return default if v is None else v


def _persist_toggle(section: str, key: str, value):
    """Immediately persist a single toggle + refresh live adapters."""
    from ..config_manager import load_config_file, save_config_file
    data = load_config_file()
    data.setdefault(section, {})[key] = value
    save_config_file(data)
    manager.refresh_config()


def _live_switch(label: str, *, value: bool, section: str, key: str, tooltip: str = ""):
    """A switch that persists on every change, with an inline ✓ Saved flash."""
    with ui.row().classes("items-center gap-2"):
        def _on_change(e):
            _persist_toggle(section, key, bool(e.value))
            status.text = "✓ Saved"
            status.set_visibility(True)
            ui.timer(1.5, lambda: status.set_visibility(False), once=True)

        sw = ui.switch(label, value=value, on_change=_on_change).props("dense")
        if tooltip:
            sw.tooltip(tooltip)
        status = ui.label("").classes("text-xs text-green-600")
        status.set_visibility(False)
    return sw


def _panel_acp(current: dict):
    from ..config_manager import load_config_file

    import shutil
    raw_cli = _g(current, "acp", "kiro_cli_path", "kiro-cli")
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

    ws_data = current.get("_workspaces", {})
    ws_names = list(ws_data.keys()) if ws_data else ["default"]
    fixed_ws_value = _g(current, "acp", "fixed_workspace") or (
        "default" if "default" in ws_names else ws_names[0]
    )

    idle = ui.number(
        "Idle Timeout (s)",
        value=_g(current, "acp", "idle_timeout", 300),
        min=0, step=60,
    ).classes("w-full mt-2").props(
        'hint="Reap idle ACP sessions after N seconds (0 to disable)"'
    )
    prompt_to = ui.number(
        "Response Timeout (s)",
        value=_g(current, "acp", "response_timeout", 3600),
        min=60, step=60,
    ).classes("w-full mt-2").props(
        'hint="Max wait for a single kiro-cli response"'
    )

    with ui.row().classes("w-full gap-4 mt-2 items-end"):
        ws_mode = ui.select(
            ["per_chat", "fixed"],
            value=_g(current, "acp", "workspace_mode", "per_chat"),
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
        acp = data.setdefault("acp", {})
        acp["kiro_cli_path"] = cli_path.value.strip() or "kiro-cli"
        acp["workspace_mode"] = ws_mode.value
        acp["fixed_workspace"] = fixed_ws.value or "default"
        acp["idle_timeout"] = int(idle.value or 300)
        acp["response_timeout"] = int(prompt_to.value or 3600)
        _write(data)
        ui.notify("ACP settings saved (restart daemon to apply)", type="positive")

    _save_button(save)


# ── Panel: Adapters ──

def _panel_adapters(current: dict):
    from ..config_manager import load_config_file

    # ── Telegram ──
    with _adapter_group("Telegram", "send", _g(current, "telegram", "enabled", False)) as grp:
        tg = ui.input(
            "Bot Token",
            value=_g(current, "telegram", "bot_token", ""),
            placeholder="123456:ABC-DEF...",
            password=True, password_toggle_button=True,
        ).classes("w-full")
        tg.bind_enabled_from(grp.enabled, "value")
        tg_enabled = grp.enabled
        tg_require_auth = _live_switch(
            "Require authorization",
            value=bool(_g(current, "telegram", "require_auth", True)),
            section="telegram", key="require_auth",
            tooltip="Restrict access to users on the allowlist. TG bot handles "
                    "are public — keep this on unless you're testing.",
        )
        tg_require_auth.bind_enabled_from(grp.enabled, "value")
        _allowlist_section("telegram", current, tg_require_auth)

    # ── Lark / Feishu ──
    with _adapter_group("Lark / Feishu", "business", _g(current, "lark", "enabled", False)) as grp:
        with ui.row().classes("w-full gap-4"):
            lark_id = ui.input(
                "App ID",
                value=_g(current, "lark", "app_id", ""),
            ).classes("flex-grow")
            lark_secret = ui.input(
                "App Secret",
                value=_g(current, "lark", "app_secret", ""),
                password=True, password_toggle_button=True,
            ).classes("flex-grow")
        lark_dom = ui.select(
            ["feishu", "lark"],
            value=_g(current, "lark", "domain", "feishu"),
            label="Domain",
        ).classes("w-48 mt-2")
        for el in (lark_id, lark_secret, lark_dom):
            el.bind_enabled_from(grp.enabled, "value")
        lark_enabled = grp.enabled
        lark_require_auth = _live_switch(
            "Require authorization",
            value=bool(_g(current, "lark", "require_auth", False)),
            section="lark", key="require_auth",
            tooltip="Restrict access via claim-token allowlist. Off = anyone "
                    "in your tenant who can add the bot is allowed.",
        )
        lark_require_auth.bind_enabled_from(grp.enabled, "value")
        _allowlist_section("lark", current, lark_require_auth)

    # ── Discord ──
    with _adapter_group("Discord", "forum", _g(current, "discord", "enabled", False)) as grp:
        discord = ui.input(
            "Bot Token",
            value=_g(current, "discord", "bot_token", ""),
            placeholder="MTIzNDU2Nzg5...",
            password=True, password_toggle_button=True,
        ).classes("w-full")
        discord.bind_enabled_from(grp.enabled, "value")
        discord_enabled = grp.enabled
        discord_require_auth = _live_switch(
            "Require authorization",
            value=bool(_g(current, "discord", "require_auth", False)),
            section="discord", key="require_auth",
            tooltip="Restrict access via claim-token allowlist. Off = anyone "
                    "sharing a server with the bot (or DM'ing it) is allowed.",
        )
        discord_require_auth.bind_enabled_from(grp.enabled, "value")
        _allowlist_section("discord", current, discord_require_auth)

    with ui.row().classes("items-center gap-1 mt-2 text-gray-500"):
        ui.icon("info").classes("text-base")
        ui.label("Disabling an adapter stops it immediately if running.") \
            .classes("text-xs")

    def save():
        data = load_config_file()
        t = data.setdefault("telegram", {})
        t["bot_token"] = tg.value.strip()
        t["enabled"] = bool(tg_enabled.value)
        t["require_auth"] = bool(tg_require_auth.value)
        lk = data.setdefault("lark", {})
        lk["app_id"] = lark_id.value.strip()
        lk["app_secret"] = lark_secret.value.strip()
        lk["domain"] = lark_dom.value
        lk["enabled"] = bool(lark_enabled.value)
        lk["require_auth"] = bool(lark_require_auth.value)
        dc = data.setdefault("discord", {})
        dc["bot_token"] = discord.value.strip()
        dc["enabled"] = bool(discord_enabled.value)
        dc["require_auth"] = bool(discord_require_auth.value)
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


# ── Panel: Heartbeat (scheduled tasks) ──

_CRON_PRESETS = [
    ("Every hour",             "0 * * * *"),
    ("Every 15 min",           "*/15 * * * *"),
    ("Every 5 min",            "*/5 * * * *"),
    ("Every day at 9am",       "0 9 * * *"),
    ("Weekdays at 9am",        "0 9 * * 1-5"),
    ("Every Monday at 9am",    "0 9 * * 1"),
    ("Custom…",                ""),
]
_INTERVAL_UNITS = {"minutes": 60, "hours": 3600, "days": 86400, "weeks": 604800}
_PLATFORM_CHOICES = ["telegram", "lark", "discord", "webchat"]


def _panel_heartbeat(current: dict):
    from ..config_manager import load_config_file
    from ..server import get_scheduler

    rows: list[dict] = []
    for t in current.get("tasks") or []:
        if isinstance(t, dict):
            rows.append(dict(t))  # mutable copy

    def _add():
        rows.append({
            "name": "new-task",
            "enabled": True,
            "every_seconds": 3600,
            "cron": "",
            "workspace": "default",
            "prompt": "",
            "target_platform": "webchat",
            "target_chat_ids": [],
        })
        _rows_view.refresh()

    def _delete(idx: int):
        rows.pop(idx)
        _rows_view.refresh()

    async def _run_now(name: str):
        sched = get_scheduler()
        if sched is None:
            ui.notify("Scheduler not running", type="negative")
            return
        ok = await sched.run_once(name)
        ui.notify("Fired" if ok else "Failed — check logs",
                  type="positive" if ok else "negative")

    @ui.refreshable
    def _rows_view():
        if not rows:
            ui.label("No scheduled tasks yet. Click + Add task to create one.") \
                .classes("text-sm text-gray-500")
        for i, row in enumerate(rows):
            _task_card(i, row, _delete, _run_now)

    _rows_view()

    with ui.row().classes("w-full justify-between mt-2"):
        ui.button("Add task", icon="add", on_click=_add) \
            .props("flat dense size=sm color=primary")

        def _save():
            data = load_config_file()
            cleaned: list[dict] = []
            for r in rows:
                # Drop obviously broken tasks so the file stays valid.
                name = str(r.get("name") or "").strip()
                if not name:
                    continue
                entry: dict = {
                    "name": name,
                    "enabled": bool(r.get("enabled", True)),
                    "workspace": str(r.get("workspace") or "default"),
                    "prompt": str(r.get("prompt") or ""),
                    "target_platform": str(r.get("target_platform") or "webchat"),
                    "target_chat_ids": list(r.get("target_chat_ids") or []),
                }
                if (r.get("cron") or "").strip():
                    entry["cron"] = str(r["cron"]).strip()
                else:
                    entry["every_seconds"] = int(r.get("every_seconds") or 3600)
                cleaned.append(entry)
            data["tasks"] = cleaned
            _write(data)
            sched = get_scheduler()
            if sched is not None:
                sched.reload()
            ui.notify("Tasks saved — scheduler restarted", type="positive")

        ui.button("Save", icon="save", on_click=_save, color="primary") \
            .props("dense")


def _task_card(idx: int, row: dict, on_delete, on_run_now):
    """Inline-editable card for a single task."""
    import time as _time
    from datetime import datetime as _dt
    from ..config import TaskConfig
    from ..scheduler import next_fire_at

    with ui.card().classes("w-full p-3 mb-2"):
        # ── Header row: name + enabled + run-now + delete ──
        with ui.row().classes("w-full items-center gap-2"):
            ui.icon("schedule", color="primary").classes("text-base")
            name = ui.input(value=row.get("name", ""), placeholder="task name") \
                .props("dense outlined").classes("flex-grow")
            name.on_value_change(lambda e: row.__setitem__("name", e.value))

            enabled = ui.switch(value=bool(row.get("enabled", True))).props("dense")
            enabled.on_value_change(lambda e: row.__setitem__("enabled", bool(e.value)))

            async def _run():
                await on_run_now(row.get("name", ""))
            ui.button(icon="play_arrow", on_click=_run) \
                .props("flat dense round size=sm color=primary") \
                .tooltip("Run this task now")
            ui.button(icon="delete_outline", on_click=lambda _=None, i=idx: on_delete(i)) \
                .props("flat dense round size=sm color=grey") \
                .tooltip("Delete task")

        ui.separator().classes("my-2")

        # ── Schedule ──
        mode = "cron" if (row.get("cron") or "").strip() else "interval"
        next_label = ui.label().classes("text-xs text-gray-500")

        def _update_preview():
            try:
                tc = TaskConfig(
                    name=row.get("name") or "",
                    enabled=True,
                    every_seconds=int(row.get("every_seconds") or 0),
                    workspace="x", prompt="x",
                    target_platform="webchat", target_chat_ids=[],
                    cron=(row.get("cron") or "").strip(),
                )
                ts = next_fire_at(tc)
                if ts:
                    next_label.text = "Next fires at: " + _dt.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                    next_label.classes(replace="text-xs text-gray-500")
                else:
                    next_label.text = "⚠️  Invalid schedule"
                    next_label.classes(replace="text-xs text-red-600")
            except Exception as e:
                next_label.text = f"⚠️  {e}"
                next_label.classes(replace="text-xs text-red-600")

        with ui.row().classes("w-full items-center gap-3"):
            ui.label("Schedule").classes("text-xs text-gray-500 w-20")
            mode_sel = ui.select(["interval", "cron"], value=mode) \
                .props("dense outlined").classes("w-32")

            # Interval inputs
            int_row = ui.row().classes("items-center gap-2")
            with int_row:
                ui.label("Every").classes("text-xs text-gray-500")
                secs = int(row.get("every_seconds") or 3600)
                unit = "hours"
                for u, factor in _INTERVAL_UNITS.items():
                    if secs % factor == 0:
                        unit = u
                        break
                qty = secs // _INTERVAL_UNITS[unit] or 1
                qty_input = ui.number(value=qty, min=1, step=1).props("dense outlined").classes("w-24")
                unit_sel = ui.select(list(_INTERVAL_UNITS.keys()), value=unit) \
                    .props("dense outlined").classes("w-28")

                def _interval_changed(_=None):
                    row["every_seconds"] = int(qty_input.value or 1) * _INTERVAL_UNITS[unit_sel.value]
                    row["cron"] = ""
                    _update_preview()
                qty_input.on_value_change(_interval_changed)
                unit_sel.on_value_change(_interval_changed)

            # Cron inputs
            cron_row = ui.row().classes("items-center gap-2")
            with cron_row:
                preset_sel = ui.select(
                    [label for label, _ in _CRON_PRESETS],
                    value="Custom…" if row.get("cron") and row.get("cron") not in {v for _, v in _CRON_PRESETS} else (
                        next((label for label, v in _CRON_PRESETS if v == row.get("cron")), "Custom…")
                    ),
                ).props("dense outlined").classes("w-56")
                cron_input = ui.input(value=row.get("cron") or "0 9 * * *") \
                    .props("dense outlined").classes("w-40")

                def _preset_changed(e):
                    choice = e.value
                    mapped = dict(_CRON_PRESETS).get(choice, "")
                    if mapped:
                        cron_input.value = mapped
                    row["cron"] = cron_input.value.strip()
                    row["every_seconds"] = 0
                    _update_preview()
                preset_sel.on_value_change(_preset_changed)

                def _cron_changed(e):
                    row["cron"] = (e.value or "").strip()
                    row["every_seconds"] = 0
                    _update_preview()
                cron_input.on_value_change(_cron_changed)

            def _mode_changed(e):
                is_cron = e.value == "cron"
                int_row.set_visibility(not is_cron)
                cron_row.set_visibility(is_cron)
                if is_cron:
                    row["cron"] = cron_input.value.strip() or "0 9 * * *"
                    row["every_seconds"] = 0
                else:
                    row["cron"] = ""
                    row["every_seconds"] = int(qty_input.value or 1) * _INTERVAL_UNITS[unit_sel.value]
                _update_preview()
            mode_sel.on_value_change(_mode_changed)

            int_row.set_visibility(mode == "interval")
            cron_row.set_visibility(mode == "cron")

        with ui.row().classes("w-full items-center gap-3"):
            ui.label("").classes("w-20")
            next_label
        _update_preview()

        # ── Workspace + target ──
        from ..config import config
        ws_choices = list(config.workspaces.keys()) or ["default"]
        with ui.row().classes("w-full items-center gap-3 mt-2"):
            ui.label("Workspace").classes("text-xs text-gray-500 w-20")
            ws_sel = ui.select(ws_choices, value=row.get("workspace") or ws_choices[0]) \
                .props("dense outlined").classes("w-48")
            ws_sel.on_value_change(lambda e: row.__setitem__("workspace", e.value))

            ui.label("Target").classes("text-xs text-gray-500 ml-4")
            plat_sel = ui.select(
                _PLATFORM_CHOICES,
                value=row.get("target_platform") or "webchat",
            ).props("dense outlined").classes("w-36")

            ids_label = ui.label("(broadcast to all allowed users; webchat broadcasts to all open tabs)") \
                .classes("text-xs text-gray-500")
            ids_input = ui.input(
                value=",".join(str(x) for x in (row.get("target_chat_ids") or [])),
                placeholder="id1, id2 (empty = broadcast)",
            ).props("dense outlined").classes("w-64")

            def _parse_ids(raw: str, platform: str):
                out = []
                for p in raw.split(","):
                    p = p.strip()
                    if not p:
                        continue
                    if platform in ("telegram", "discord") and p.lstrip("-").isdigit():
                        out.append(int(p))
                    else:
                        out.append(p)
                return out

            def _plat_changed(e):
                row["target_platform"] = e.value
                ids_input.set_visibility(e.value != "webchat")
                ids_label.set_visibility(e.value == "webchat")
            plat_sel.on_value_change(_plat_changed)
            ids_input.on_value_change(
                lambda e: row.__setitem__("target_chat_ids", _parse_ids(e.value, plat_sel.value))
            )
            ids_input.set_visibility(row.get("target_platform") != "webchat")
            ids_label.set_visibility(row.get("target_platform") == "webchat")

        # ── Prompt ──
        with ui.row().classes("w-full items-start gap-3 mt-2"):
            ui.label("Prompt").classes("text-xs text-gray-500 w-20 mt-2")
            prompt_input = ui.textarea(value=row.get("prompt") or "") \
                .props('dense outlined input-style="min-height: 5rem; resize: vertical"') \
                .classes("flex-grow")
            prompt_input.on_value_change(lambda e: row.__setitem__("prompt", e.value))


# ── Telegram allowlist helpers ──

def _allowlist_section(section: str, current: dict, require_auth_switch):
    """Show authorized ids + claim-token button for any adapter section.

    Visible only when `require_auth` is on. Each row has a delete button
    that takes effect immediately.
    """
    from ..security import create_claim, active_claim, revoke_user
    from ..config_manager import load_config_file
    import time as _time

    container = ui.column().classes("w-full gap-1")
    container.bind_visibility_from(require_auth_switch, "value")
    with container:
        ui.separator().classes("my-2")

        with ui.row().classes("w-full items-center gap-2"):
            ui.icon("verified_user", color="primary").classes("text-base")
            ui.label("Authorized users").classes("text-sm font-semibold text-gray-700")
            ui.space()
            header_count = ui.label().classes("text-xs text-gray-500")

        list_col = ui.column().classes("w-full gap-0")

        def _render_list():
            list_col.clear()
            fresh = load_config_file().get(section) or {}
            ids = fresh.get("allowed_user_ids") or []
            meta = fresh.get("allowed_users_meta") or {}
            header_count.text = f"{len(ids)} user(s)"
            with list_col:
                if not ids:
                    ui.label("No authorized users. Generate a claim token and DM "
                             "the bot `/claim <token>` to authorize yourself.") \
                        .classes("text-xs text-amber-700")
                    return
                for uid in ids:
                    uname = meta.get(str(uid), "")
                    with ui.row().classes("items-center gap-2 w-full"):
                        ui.label(str(uid)).classes("text-xs font-mono text-gray-700")
                        if uname:
                            ui.label(f"@{uname}").classes("text-xs text-gray-500")
                        ui.space()
                        def _revoke(uid=uid):
                            if revoke_user(section, uid):
                                manager.refresh_config()
                                ui.notify(f"Revoked {uid}", type="positive")
                                _render_list()
                        ui.button(icon="delete_outline", on_click=_revoke) \
                            .props("flat dense round size=xs color=grey") \
                            .tooltip("Revoke access (takes effect immediately)")

        _render_list()

        token_label = ui.label().classes("text-sm font-mono text-gray-800 mt-2")
        expiry_label = ui.label().classes("text-xs text-gray-500")

        def _render_token(token: str, expires_at: int):
            mins = max(0, int((expires_at - _time.time()) / 60))
            token_label.text = f"Token: {token}"
            expiry_label.text = f"Valid for ~{mins} min. DM the bot: /claim {token}"

        def _sync_token():
            live = active_claim(section)
            if live:
                token_label.set_visibility(True)
                expiry_label.set_visibility(True)
                _render_token(live["token"], int(live["expires_at"]))
            else:
                token_label.set_visibility(False)
                expiry_label.set_visibility(False)

        _sync_token()

        # Auto-pick up /claim done from a chat client without a page refresh.
        # Track the last-seen signature so the DOM only rebuilds when it changes.
        _state = {"sig": None}

        def _poll():
            fresh = load_config_file().get(section) or {}
            sig = (tuple(fresh.get("allowed_user_ids") or []),
                   tuple(sorted((fresh.get("allowed_users_meta") or {}).items())))
            if sig != _state["sig"]:
                _state["sig"] = sig
                _render_list()
            _sync_token()  # token file gets deleted on successful /claim
        _poll()  # seed sig
        poll_timer = ui.timer(3.0, _poll)
        poll_timer.active = bool(require_auth_switch.value)
        require_auth_switch.on_value_change(
            lambda e: setattr(poll_timer, "active", bool(e.value))
        )

        def _on_generate():
            create_claim(section)
            _sync_token()
            ui.notify("Claim token generated (valid 15 min)", type="positive")

        ui.button("Generate claim token", icon="key", on_click=_on_generate) \
            .props("flat dense size=sm color=primary").classes("mt-1")


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
    """Save config — sections are kept as-is; save_config_file skips empty scalars."""
    from ..config_manager import save_config_file
    save_config_file(data)
