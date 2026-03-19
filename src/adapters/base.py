"""Base adapter interface for chat platforms."""

from abc import ABC, abstractmethod

from ..acp.client import PermissionRequest, ToolCallInfo


class BaseAdapter(ABC):
    """All platform adapters must implement this interface."""

    @abstractmethod
    async def send_text(self, chat_id: str, text: str):
        """Send a final text message."""

    @abstractmethod
    async def send_streaming_update(self, chat_id: str, chunk: str, accumulated: str):
        """Called on each streaming text chunk."""

    @abstractmethod
    async def send_tool_status(self, chat_id: str, tool: ToolCallInfo):
        """Notify user about tool call start/update/completion."""

    @abstractmethod
    async def request_permission(self, chat_id: str, request: PermissionRequest) -> str:
        """Ask user for permission. Return 'allow_once' | 'allow_always' | 'deny'."""

    @abstractmethod
    async def send_image(self, chat_id: str, path: str):
        """Send an image file to the chat."""

    async def start(self):
        """Start the adapter (connect to platform)."""

    async def stop(self):
        """Stop the adapter."""
