"""Base adapter interface for chat platforms."""


class BaseAdapter:
    """Base class for platform adapters."""

    async def start(self):
        """Start the adapter (connect to platform)."""

    async def stop(self):
        """Stop the adapter."""
