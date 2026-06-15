"""Shared page shell: top navigation + page wrapper.

All admin pages (Dashboard, Config, Chat) wrap their content in `page_shell`
to get a consistent top bar, active-tab highlight, and layout spacing.
"""

from contextlib import contextmanager

from nicegui import ui

from .. import __version__


NAV = [
    ("dashboard", "Dashboard", "dashboard", "/"),
    ("chat", "Chat", "chat", "/chat"),
    ("sessions", "Sessions", "forum", "/sessions"),
    ("settings", "Settings", "settings", "/settings"),
]


@contextmanager
def page_shell(current: str):
    """Render the shared top bar and open a main content column.

    Usage:
        with page_shell(current="dashboard"):
            ui.label("page body...")
    """
    ui.query("body").classes("bg-gray-50")
    # Kill NiceGUI's default page padding so our bar spans full width
    ui.query(".nicegui-content").classes("p-0")

    with ui.header(elevated=False).classes(
        "bg-white border-b border-gray-200 py-2 px-0"
    ).style("color: inherit"):
        with ui.row().classes("w-full max-w-6xl mx-auto px-4 items-center"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("hub", color="primary").classes("text-2xl")
                ui.label("Kiro2Chat").classes("text-lg font-bold text-gray-800")
                ui.label(f"v{__version__}").classes("text-xs text-gray-400 ml-1")

            ui.space()

            with ui.row().classes("items-center gap-1"):
                for key, label, icon, href in NAV:
                    active = key == current
                    classes = (
                        "px-3 py-1.5 rounded-md text-sm font-medium no-underline flex items-center gap-1 "
                        + (
                            "bg-blue-50 text-blue-600"
                            if active
                            else "text-gray-600 hover:bg-gray-100 hover:text-gray-900"
                        )
                    )
                    with ui.link(target=href).classes(classes):
                        ui.icon(icon).classes("text-base")
                        ui.label(label)

            _render_user_menu()

    with ui.column().classes("w-full max-w-6xl mx-auto py-6 px-4 gap-6"):
        yield


def _render_user_menu():
    """Show the signed-in user + a logout link when web auth is enabled."""
    from ..config import config
    if not config.auth.enabled:
        return
    from .auth import current_user
    user = current_user()
    if not user:
        return
    label = user.get("username") or user.get("name") or user.get("email") or "account"
    with ui.row().classes("items-center gap-1 ml-2 pl-2 border-l border-gray-200"):
        ui.icon("account_circle", color="primary").classes("text-xl")
        ui.label(label).classes("text-sm text-gray-600 max-w-[12rem] truncate")
        with ui.link(target="/auth/logout").classes(
            "px-2 py-1.5 rounded-md text-sm text-gray-600 "
            "hover:bg-gray-100 hover:text-gray-900 no-underline flex items-center"
        ).tooltip("Sign out"):
            ui.icon("logout").classes("text-base")
