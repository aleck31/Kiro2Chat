"""Sessions page — browse kiro-cli's on-disk sessions grouped by workspace (cwd).

Read-only discovery view over ~/.kiro/sessions/cli/. Lets the user:
  - see every kiro session grouped by its workspace directory
  - delete sessions (single or multi-select batch) via `kiro-cli chat --delete-session`
  - adopt a cwd as a kiro2chat workspace (writes config.toml [workspaces.*])

The disk scan (~40ms) is cached in page state; checkbox toggles only refresh
the lightweight action bar, not the whole list. Re-scan happens on Refresh,
delete, or adopt.
"""

from pathlib import Path

from nicegui import run, ui

from ..acp import session_store as store
from .layout import page_shell


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n // 1024} KB"
    return f"{n / 1024 / 1024:.1f} MB"


def _fmt_when(iso: str) -> str:
    return iso.replace("T", " ") if iso else "—"


def register():
    @ui.page("/sessions")
    def sessions_page():
        # Per-client page state (scan cache + selection set)
        state: dict = {"groups": [], "selected": set()}

        def _reload():
            state["groups"] = store.list_by_workspace()
            state["selected"].clear()
            _stats.refresh()
            _view.refresh()
            _action_bar.refresh()

        with page_shell(current="sessions"):
            with ui.row().classes("w-full items-center"):
                ui.label("Sessions").classes("text-lg font-semibold text-gray-700")
                ui.space()
                ui.button("Refresh", icon="refresh", on_click=_reload) \
                    .props("flat dense size=sm color=primary")

            ui.label(
                "All kiro-cli sessions on disk, grouped by workspace directory. "
                "Select multiple to delete in batch, or adopt a directory as a workspace."
            ).classes("text-sm text-gray-500 -mt-2")

            # ── Selection helpers ──

            def _all_selectable() -> list[store.StoredSession]:
                return [s for g in state["groups"] for s in g.sessions if not s.locked]

            def _toggle(sid: str, on: bool):
                if on:
                    state["selected"].add(sid)
                else:
                    state["selected"].discard(sid)
                _action_bar.refresh()

            def _toggle_group(g: store.WorkspaceGroup, on: bool):
                for s in g.sessions:
                    if s.locked:
                        continue
                    if on:
                        state["selected"].add(s.session_id)
                    else:
                        state["selected"].discard(s.session_id)
                _view.refresh()
                _action_bar.refresh()

            def _select_all(on: bool):
                if on:
                    state["selected"] = {s.session_id for s in _all_selectable()}
                else:
                    state["selected"].clear()
                _view.refresh()
                _action_bar.refresh()

            # ── Batch action bar (refreshes independently of the list) ──

            @ui.refreshable
            def _action_bar():
                sel = state["selected"]
                if not sel:
                    return
                # total size of selected
            @ui.refreshable
            def _action_bar():
                selectable = _all_selectable()
                if not selectable:
                    return
                sel = state["selected"]
                by_id = {s.session_id: s for s in selectable}
                total = sum(by_id[sid].size_bytes for sid in sel if sid in by_id)
                all_on = len(sel) == len(selectable) and len(sel) > 0

                with ui.row().classes(
                    "w-full items-center gap-3 px-4 py-2 bg-gray-50 border border-gray-200 "
                    "rounded-md sticky top-2 z-10"
                ):
                    ui.checkbox(
                        "Select all", value=all_on,
                        on_change=lambda e: _select_all(e.value),
                    ).props("dense").classes("text-sm shrink-0")

                    if sel:
                        ui.separator().props("vertical").classes("h-5")
                        ui.label(f"{len(sel)} selected").classes(
                            "text-sm font-medium text-blue-700")
                        ui.label(f"· {_fmt_size(total)}").classes("text-xs text-gray-400")
                    ui.space()
                    if sel:
                        ui.button("Delete selected", icon="delete",
                                  on_click=lambda: _batch_delete_dialog()) \
                            .props("dense size=sm color=negative")
                        ui.button("Clear", icon="clear",
                                  on_click=lambda: _select_all(False)) \
                            .props("flat dense size=sm color=grey")

            # ── Main list view (renders from cached state["groups"]) ──

            @ui.refreshable
            def _stats():
                groups = state["groups"]
                n_sessions = sum(len(g.sessions) for g in groups)
                n_locked = sum(1 for g in groups for s in g.sessions if s.locked)
                with ui.row().classes("w-full gap-3"):
                    _stat("folder", "Workspaces", str(len(groups)))
                    _stat("forum", "Sessions", str(n_sessions))
                    _stat("lock", "Locked", str(n_locked),
                          color="amber" if n_locked else "primary")

            @ui.refreshable
            def _view():
                groups = state["groups"]
                if not groups:
                    with ui.card().classes("w-full p-6 items-center text-gray-400"):
                        ui.icon("inbox").classes("text-4xl")
                        ui.label("No sessions found under ~/.kiro/sessions/cli/") \
                            .classes("text-sm")
                    return

                for g in groups:
                    _group_card(g)

            def _group_card(g: store.WorkspaceGroup):
                grp_selectable = [s for s in g.sessions if not s.locked]
                grp_all = bool(grp_selectable) and all(
                    s.session_id in state["selected"] for s in grp_selectable
                )
                with ui.card().classes("w-full p-0"):
                    with ui.row().classes(
                        "w-full items-center flex-nowrap px-4 py-2.5 bg-gray-50 "
                        "border-b border-gray-200 gap-2"
                    ):
                        if grp_selectable:
                            ui.checkbox(
                                value=grp_all,
                                on_change=lambda e, grp=g: _toggle_group(grp, e.value),
                            ).props("dense").classes("shrink-0") \
                             .tooltip("Select all in this workspace")
                        ui.icon("folder").classes("text-lg text-gray-500 shrink-0")
                        ui.label(g.cwd).classes("font-mono text-sm text-gray-700 truncate min-w-0") \
                            .tooltip(g.cwd)
                        ui.label(f"({len(g.sessions)})").classes("text-xs text-gray-400 shrink-0")
                        ui.space()
                        if g.in_config:
                            with ui.row().classes("items-center gap-1 text-green-600"):
                                ui.icon("check_circle").classes("text-base")
                                ui.label(g.config_name).classes("text-xs font-medium")
                        else:
                            ui.button(
                                "Add as Workspace", icon="add_box",
                                on_click=lambda grp=g: _adopt_dialog(grp),
                            ).props("flat dense size=sm color=primary")

                    for s in g.sessions:
                        _session_row(s)

            def _session_row(s: store.StoredSession):
                with ui.row().classes(
                    "w-full items-center flex-nowrap px-4 py-2 border-b border-gray-100 "
                    "hover:bg-gray-50 gap-3 text-sm"
                ):
                    if s.locked:
                        ui.element("div").classes("w-8 shrink-0")  # checkbox spacer
                    else:
                        ui.checkbox(
                            value=s.session_id in state["selected"],
                            on_change=lambda e, sid=s.session_id: _toggle(sid, e.value),
                        ).props("dense").classes("shrink-0")

                    title = s.title or "(untitled)"
                    # min-w-0 lets this flex item shrink below content width so
                    # `truncate` actually clips long titles instead of wrapping.
                    ui.label(title).classes("flex-grow min-w-0 truncate text-gray-700") \
                        .tooltip(title)

                    if s.locked:
                        ui.icon("lock").classes("text-sm text-amber-500 shrink-0") \
                            .tooltip("In use by a running process")

                    ui.label(_fmt_size(s.size_bytes)) \
                        .classes("w-20 shrink-0 text-right text-xs text-gray-500")
                    ui.label(_fmt_when(s.updated_at)) \
                        .classes("w-36 shrink-0 text-xs text-gray-500")
                    ui.label(s.session_id[:8]) \
                        .classes("w-20 shrink-0 font-mono text-xs text-gray-400") \
                        .tooltip(s.session_id)

                    ui.button(icon="delete_outline",
                              on_click=lambda sess=s: _delete_dialog(sess)) \
                        .props("flat dense round size=sm color=grey") \
                        .classes("shrink-0") \
                        .tooltip("Delete this session")

            # ── Adopt as workspace ──

            def _adopt_dialog(g: store.WorkspaceGroup):
                default_name = Path(g.cwd).name or "workspace"
                # Build session options: {sid: "title · size · date"}, newest first
                opts: dict[str, str] = {}
                for s in g.sessions:  # already sorted newest-first
                    label = s.title or "(untitled)"
                    if len(label) > 36:
                        label = label[:35] + "…"
                    opts[s.session_id] = f"{label} · {_fmt_size(s.size_bytes)} · {s.updated_at[:10]}"
                default_sid = g.latest_session_id

                with ui.dialog() as dialog, ui.card().classes("w-[28rem] gap-3"):
                    ui.label("Add as Workspace").classes("text-base font-semibold")
                    ui.label(g.cwd).classes("text-xs font-mono text-gray-500 break-all")
                    name_in = ui.input("Workspace name", value=default_name) \
                        .classes("w-full").props("dense outlined")
                    sess_sel = ui.select(
                        options=opts, value=default_sid,
                        label="Resume session",
                    ).classes("w-full").props("dense outlined")
                    ui.label().classes("text-xs text-gray-400") \
                        .bind_text_from(
                            sess_sel, "value",
                            lambda v: f"Will resume session {v[:8]}…" if v else "No session selected",
                        )
                    with ui.row().classes("w-full justify-end gap-2"):
                        ui.button("Cancel", on_click=dialog.close).props("flat dense size=sm")
                        ui.button("Add", color="primary",
                                  on_click=lambda: _do_adopt(
                                      dialog, name_in.value, g, sess_sel.value)) \
                            .props("dense size=sm")
                dialog.open()

            def _do_adopt(dialog, name: str, g: store.WorkspaceGroup, session_id: str):
                name = (name or "").strip()
                if not name:
                    ui.notify("Name required", type="warning")
                    return
                from ..config_manager import load_config_file, save_config_file
                from ..config import reload as reload_config

                data = load_config_file()
                ws = data.get("_workspaces", {}) or {}
                if name in ws:
                    ui.notify(f"Workspace '{name}' already exists", type="warning")
                    return
                ws[name] = {"path": g.cwd, "session_id": session_id or g.latest_session_id}
                data["_workspaces"] = ws
                save_config_file(data)
                reload_config()
                dialog.close()
                ui.notify(f"Added workspace '{name}'", type="positive")
                _reload()

            # ── Delete (single) ──

            def _delete_dialog(s: store.StoredSession):
                if s.locked:
                    ui.notify("Session is in use (locked). Stop the bot/daemon first.",
                              type="warning")
                    return
                with ui.dialog() as dialog, ui.card().classes("w-96 gap-3"):
                    ui.label("Delete session?").classes("text-base font-semibold")
                    ui.label(s.title or s.session_id).classes("text-sm text-gray-600 break-all")
                    ui.label(f"{s.session_id}  ·  {_fmt_size(s.size_bytes)}") \
                        .classes("text-xs font-mono text-gray-400 break-all")
                    ui.label("This removes the session files permanently.") \
                        .classes("text-xs text-red-500")
                    with ui.row().classes("w-full justify-end gap-2"):
                        ui.button("Cancel", on_click=dialog.close).props("flat dense size=sm")
                        ui.button("Delete", color="negative",
                                  on_click=lambda: _do_delete(dialog, s)) \
                            .props("dense size=sm")
                dialog.open()

            async def _do_delete(dialog, s: store.StoredSession):
                dialog.close()
                ok, msg = await run.io_bound(store.delete_session, s.session_id)
                if ok:
                    ui.notify(f"Deleted: {s.title or s.session_id[:8]}", type="positive")
                else:
                    ui.notify(f"Delete failed: {msg}", type="negative")
                _reload()

            # ── Delete (batch) ──

            def _batch_delete_dialog():
                sel = list(state["selected"])
                if not sel:
                    return
                by_id = {s.session_id: s for s in _all_selectable()}
                total = sum(by_id[sid].size_bytes for sid in sel if sid in by_id)
                with ui.dialog() as dialog, ui.card().classes("w-96 gap-3"):
                    ui.label(f"Delete {len(sel)} sessions?").classes("text-base font-semibold")
                    ui.label(f"Total {_fmt_size(total)} will be permanently removed.") \
                        .classes("text-sm text-gray-600")
                    ui.label("Locked sessions are excluded automatically.") \
                        .classes("text-xs text-gray-400")
                    with ui.row().classes("w-full justify-end gap-2"):
                        ui.button("Cancel", on_click=dialog.close).props("flat dense size=sm")
                        ui.button(f"Delete {len(sel)}", color="negative",
                                  on_click=lambda: _do_batch_delete(dialog, sel)) \
                            .props("dense size=sm")
                dialog.open()

            async def _do_batch_delete(dialog, sids: list[str]):
                dialog.close()
                n = len(sids)
                prog = ui.notification(
                    f"Deleting 0/{n}…", spinner=True, timeout=None, close_button=False
                )

                def _progress(done: int, total: int):
                    prog.message = f"Deleting {done}/{total}…"

                # Concurrent delete in a worker thread (4×cores, ≤16) keeps the
                # event loop free so the WebSocket heartbeat survives.
                ok_n, fail_n = await run.io_bound(
                    store.delete_sessions, sids, "kiro-cli", _progress
                )
                prog.dismiss()
                if fail_n:
                    ui.notify(f"Deleted {ok_n}, {fail_n} failed", type="warning")
                else:
                    ui.notify(f"Deleted {ok_n} sessions", type="positive")
                _reload()

            # Initial render: stats → action bar → list
            state["groups"] = store.list_by_workspace()
            _stats()
            _action_bar()
            _view()


def _stat(icon: str, label: str, value: str, color: str = "primary"):
    with ui.card().classes("flex-1 min-w-[160px] p-4"):
        with ui.row().classes("items-center gap-2"):
            ui.icon(icon, color=color).classes("text-xl")
            ui.label(label).classes("text-xs text-gray-500 font-medium uppercase tracking-wide")
        ui.label(value).classes("text-2xl font-bold text-gray-800 mt-1")
