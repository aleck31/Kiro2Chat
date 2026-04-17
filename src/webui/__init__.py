"""WebUI — Admin Dashboard + Settings pages."""

from ..acp.bridge import Bridge
from . import dashboard, settings


def register_pages(bridge: Bridge):
    """Register all admin UI pages."""
    dashboard.register(bridge)
    settings.register()
