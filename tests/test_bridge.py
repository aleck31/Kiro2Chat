"""Tests for Bridge."""

from unittest.mock import MagicMock

from src.acp.bridge import Bridge, _SessionInfo


def test_session_info():
    info = _SessionInfo("sid-123")
    assert info.session_id == "sid-123"
    assert info.last_active > 0
    assert info.lock is not None


def test_get_workspace_per_chat(tmp_path):
    b = Bridge(working_dir=str(tmp_path), workspace_mode="per_chat")
    ws = b._get_workspace("private.123")
    assert ws == str(tmp_path / "private.123")
    assert (tmp_path / "private.123").is_dir()


def test_get_workspace_fixed(tmp_path):
    b = Bridge(working_dir=str(tmp_path), workspace_mode="fixed")
    ws = b._get_workspace("any-chat")
    assert ws == str(tmp_path)


def test_get_workspace_group(tmp_path):
    b = Bridge(working_dir=str(tmp_path), workspace_mode="per_chat")
    ws = b._get_workspace("group.456")
    assert ws == str(tmp_path / "group.456")
    assert (tmp_path / "group.456").is_dir()


def test_start_stop():
    b = Bridge()
    b.start()
    assert b._running is True
    b.stop()
    assert b._running is False
    assert b._client is None


def test_permission_handler_stored():
    b = Bridge()
    handler = MagicMock()
    b.on_permission_request(handler)
    assert b._permission_handler is handler


def test_get_sessions():
    b = Bridge()
    # Empty initially
    assert b.get_sessions() == {}
    # Add a fake session
    from src.acp.bridge import _SessionInfo
    info = _SessionInfo("sess-123")
    b._sessions["web.private.abc"] = info
    sessions = b.get_sessions()
    assert "web.private.abc" in sessions
    assert sessions["web.private.abc"]["session_id"] == "sess-123"
    assert isinstance(sessions["web.private.abc"]["idle_seconds"], int)
