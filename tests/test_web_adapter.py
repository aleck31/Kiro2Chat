"""Tests for Web adapter."""

from unittest.mock import MagicMock

from src.adapters.web import WebAdapter, _escape, _mask
from src.adapters.base import dispatch_command


def _make_adapter():
    bridge = MagicMock()
    return WebAdapter(bridge)


# ── chat_id tests ──

def test_chat_id():
    a = _make_adapter()
    assert a._chat_id("abc123") == "web.private.abc123"


# ── escape tests ──

def test_escape_html():
    assert _escape('<b>"hello"</b>') == '&lt;b&gt;&quot;hello&quot;&lt;/b&gt;'


def test_escape_newlines():
    assert _escape("line1\nline2") == "line1<br>line2"


def test_escape_ampersand():
    assert _escape("a & b") == "a &amp; b"


# ── mask tests ──

def test_mask_long():
    assert _mask("1234567890abcdef") == "1234***cdef"


def test_mask_short():
    assert _mask("short") == "***"


def test_mask_empty():
    assert _mask("") == ""


# ── handle_command tests ──

def test_command_cancel():
    a = _make_adapter()
    container = MagicMock()
    container.__enter__ = MagicMock(return_value=container)
    container.__exit__ = MagicMock(return_value=False)
    assert a._handle_command("/cancel", "web.private.1", container) is True
    a._bridge.cancel.assert_called_once_with("web.private.1")


def test_command_clear():
    a = _make_adapter()
    container = MagicMock()
    assert a._handle_command("/reset", "web.private.1", container) is True
    a._bridge.clear.assert_called_once_with("web.private.1")


def test_command_model_no_arg():
    a = _make_adapter()
    a._bridge.get_available_models.return_value = []
    a._bridge.get_current_model.return_value = "claude"
    container = MagicMock()
    container.__enter__ = MagicMock(return_value=container)
    container.__exit__ = MagicMock(return_value=False)
    assert a._handle_command("/model", "web.private.1", container) is True


def test_command_model_with_arg():
    a = _make_adapter()
    container = MagicMock()
    container.__enter__ = MagicMock(return_value=container)
    container.__exit__ = MagicMock(return_value=False)
    assert a._handle_command("/model claude-4", "web.private.1", container) is True
    a._bridge.set_model.assert_called_once_with("web.private.1", "claude-4")


def test_command_agent_with_arg():
    a = _make_adapter()
    container = MagicMock()
    container.__enter__ = MagicMock(return_value=container)
    container.__exit__ = MagicMock(return_value=False)
    assert a._handle_command("/agent code", "web.private.1", container) is True
    a._bridge.set_mode.assert_called_once_with("web.private.1", "code")


def test_command_help():
    a = _make_adapter()
    container = MagicMock()
    container.__enter__ = MagicMock(return_value=container)
    container.__exit__ = MagicMock(return_value=False)
    assert a._handle_command("/help", "web.private.1", container) is True


def test_not_a_command():
    a = _make_adapter()
    container = MagicMock()
    assert a._handle_command("hello world", "web.private.1", container) is False


def test_plain_model_not_command():
    a = _make_adapter()
    container = MagicMock()
    assert a._handle_command("model is good", "web.private.1", container) is False


def test_command_workspace():
    a = _make_adapter()
    a._bridge.get_active_workspace.return_value = "default"
    a._bridge.get_workspaces.return_value = {"default": "/tmp/d"}
    container = MagicMock()
    container.__enter__ = MagicMock(return_value=container)
    container.__exit__ = MagicMock(return_value=False)
    assert a._handle_command("/workspace", "web.private.1", container) is True


"""Tests for dispatch_command."""


def test_workspace_show_current():
    bridge = MagicMock()
    bridge.get_active_workspace.return_value = "default"
    bridge.get_workspaces.return_value = {"default": "/tmp/d"}
    result = dispatch_command(bridge, "chat1", "/workspace")
    assert "default" in result
    assert "/tmp/d" in result


def test_workspace_list():
    bridge = MagicMock()
    bridge.get_active_workspace.return_value = "default"
    bridge.get_workspaces.return_value = {"default": "/tmp/d", "proj": "/tmp/p"}
    result = dispatch_command(bridge, "chat1", "/workspace list")
    assert "default" in result
    assert "✓" in result
    assert "proj" in result


def test_workspace_switch():
    bridge = MagicMock()
    result = dispatch_command(bridge, "chat1", "/workspace switch proj")
    bridge.switch_workspace.assert_called_once_with("chat1", "proj")
    assert "✅" in result


def test_workspace_not_command():
    bridge = MagicMock()
    result = dispatch_command(bridge, "chat1", "hello")
    assert result is None
