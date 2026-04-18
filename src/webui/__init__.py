"""WebUI — Admin Dashboard + Settings + Chat pages."""

from ..acp.bridge import Bridge
from . import chat, dashboard, settings


def register_pages(bridge: Bridge, web_adapter):
    """Register all NiceGUI pages (dashboard, settings, chat)."""
    dashboard.register(bridge)
    settings.register()
    chat.register(bridge, web_adapter)
