"""WebUI — Admin Dashboard + Config pages."""

from ..acp.bridge import Bridge
from . import dashboard, config


def register_pages(bridge: Bridge):
    """Register all admin UI pages."""
    dashboard.register(bridge)
    config.register()
