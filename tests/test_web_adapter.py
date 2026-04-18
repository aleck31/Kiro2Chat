"""Tests for Web adapter."""

from unittest.mock import MagicMock

from src.adapters.web import WebAdapter
from src.adapters.base import dispatch_command
from src.webui.chat import escape


def _make_adapter():
    bridge = MagicMock()
    return WebAdapter(bridge)


# ── chat_id tests ──

def test_chat_id():
    a = _make_adapter()
    assert a._chat_id("abc123") == "web.direct.abc123"


# ── escape tests ──

def testescape_html():
    assert escape('<b>"hello"</b>') == '&lt;b&gt;&quot;hello&quot;&lt;/b&gt;'


def testescape_newlines():
    assert escape("line1\nline2") == "line1<br>line2"


def testescape_ampersand():
    assert escape("a & b") == "a &amp; b"


# ── handle_command tests ──

def test_command_cancel():
    a = _make_adapter()
    container = MagicMock()
    container.__enter__ = MagicMock(return_value=container)
    container.__exit__ = MagicMock(return_value=False)
    assert a._handle_command("/cancel", "web.direct.1", container) is True
    a._bridge.cancel.assert_called_once_with("web.direct.1")


def test_command_clear():
    a = _make_adapter()
    container = MagicMock()
    assert a._handle_command("/reset", "web.direct.1", container) is True
    a._bridge.clear.assert_called_once_with("web.direct.1")


def test_command_model_no_arg():
    a = _make_adapter()
    a._bridge.get_available_models.return_value = []
    a._bridge.get_current_model.return_value = "claude"
    container = MagicMock()
    container.__enter__ = MagicMock(return_value=container)
    container.__exit__ = MagicMock(return_value=False)
    assert a._handle_command("/model", "web.direct.1", container) is True


def test_command_model_with_arg():
    a = _make_adapter()
    container = MagicMock()
    container.__enter__ = MagicMock(return_value=container)
    container.__exit__ = MagicMock(return_value=False)
    assert a._handle_command("/model claude-4", "web.direct.1", container) is True
    a._bridge.set_model.assert_called_once_with("web.direct.1", "claude-4")


def test_command_agent_with_arg():
    a = _make_adapter()
    container = MagicMock()
    container.__enter__ = MagicMock(return_value=container)
    container.__exit__ = MagicMock(return_value=False)
    assert a._handle_command("/agent code", "web.direct.1", container) is True
    a._bridge.set_mode.assert_called_once_with("web.direct.1", "code")


def test_command_help():
    a = _make_adapter()
    container = MagicMock()
    container.__enter__ = MagicMock(return_value=container)
    container.__exit__ = MagicMock(return_value=False)
    assert a._handle_command("/help", "web.direct.1", container) is True


def test_not_a_command():
    a = _make_adapter()
    container = MagicMock()
    assert a._handle_command("hello world", "web.direct.1", container) is False


def test_plain_model_not_command():
    a = _make_adapter()
    container = MagicMock()
    assert a._handle_command("model is good", "web.direct.1", container) is False


def test_command_workspace():
    a = _make_adapter()
    a._bridge.get_active_workspace.return_value = "default"
    a._bridge.get_workspaces.return_value = {"default": "/tmp/d"}
    container = MagicMock()
    container.__enter__ = MagicMock(return_value=container)
    container.__exit__ = MagicMock(return_value=False)
    assert a._handle_command("/workspace", "web.direct.1", container) is True


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
