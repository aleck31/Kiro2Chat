"""WebUI — Admin Dashboard + Settings + Chat + Sessions pages."""

from ..acp.bridge import Bridge
from . import chat, dashboard, sessions, settings


def register_pages(bridge: Bridge, web_adapter):
    """Register all NiceGUI pages (dashboard, settings, sessions, chat)."""
    dashboard.register(bridge)
    settings.register()
    sessions.register()
    chat.register(bridge, web_adapter)
