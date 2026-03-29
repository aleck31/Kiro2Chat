"""Tests for Web adapter."""

from unittest.mock import MagicMock

from src.adapters.web import WebAdapter, _escape


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
    a._bridge._sessions = {"web.private.1": MagicMock()}
    container = MagicMock()
    assert a._handle_command("/clear", "web.private.1", container) is True
    assert "web.private.1" not in a._bridge._sessions


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
