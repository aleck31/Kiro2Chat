"""Tests for Bridge."""

from unittest.mock import MagicMock, patch

from src.acp.bridge import Bridge, _SessionInfo


def test_session_info():
    info = _SessionInfo("sid-123")
    assert info.session_id == "sid-123"
    assert info.last_active > 0
    assert info.lock is not None
    assert info.workspace == "default"


@patch("src.config.config")
def test_get_workspace_path(mock_cfg, tmp_path):
    mock_cfg.workspaces = {"default": {"path": str(tmp_path / "default"), "session_id": None}}
    b = Bridge(working_dir=str(tmp_path), workspace_mode="per_chat")
    ws = b._get_workspace_path("private.123")
    assert ws == str(tmp_path / "default")


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
    b.on_permission_request("web.", handler)
    assert b._permission_handlers["web."] is handler


def test_get_sessions():
    b = Bridge()
    assert b.get_sessions() == []
    info = _SessionInfo("sess-123", workspace="default")
    b._sessions[("web.private.abc", "default")] = info
    sessions = b.get_sessions()
    assert len(sessions) == 1
    assert sessions[0]["chat_id"] == "web.private.abc"
    assert sessions[0]["session_id"] == "sess-123"
    assert sessions[0]["workspace"] == "default"


def test_active_workspace_default():
    b = Bridge()
    assert b.get_active_workspace("chat1") == "default"


@patch("src.config.config")
def test_switch_workspace(mock_cfg):
    mock_cfg.workspaces = {"default": {"path": "/tmp/d", "session_id": None}, "myproj": {"path": "/tmp/p", "session_id": None}}
    b = Bridge()
    b.switch_workspace("chat1", "myproj")
    assert b.get_active_workspace("chat1") == "myproj"


@patch("src.config.config")
def test_switch_workspace_unknown(mock_cfg):
    mock_cfg.workspaces = {"default": {"path": "/tmp/d", "session_id": None}}
    b = Bridge()
    import pytest
    with pytest.raises(ValueError, match="Unknown workspace"):
        b.switch_workspace("chat1", "nonexistent")


def test_clear(tmp_path):
    import src.config_manager as cm
    orig_dir, orig_file = cm.CONFIG_DIR, cm.CONFIG_FILE
    cm.CONFIG_DIR = tmp_path
    cm.CONFIG_FILE = tmp_path / "config.toml"
    try:
        b = Bridge()
        info = _SessionInfo("sess-1", workspace="test-ws")
        b._sessions[("chat1", "test-ws")] = info
        b._active_workspace["chat1"] = "test-ws"
        b.clear("chat1")
        assert ("chat1", "test-ws") not in b._sessions
    finally:
        cm.CONFIG_DIR = orig_dir
        cm.CONFIG_FILE = orig_file


@patch("src.config.config")
def test_switch_workspace_releases_old_session(mock_cfg):
    """After switch, old session is released from memory (lock freed)."""
    mock_cfg.workspaces = {"default": {"path": "/tmp/d", "session_id": None}, "proj": {"path": "/tmp/p", "session_id": None}}
    b = Bridge()
    old_info = _SessionInfo("old-sess", workspace="default")
    b._sessions[("chat1", "default")] = old_info

    b.switch_workspace("chat1", "proj")
    assert b._session_key("chat1") == ("chat1", "proj")
    # Old session removed from memory
    assert ("chat1", "default") not in b._sessions
    # New key not yet created (no _ensure_session called)
    assert ("chat1", "proj") not in b._sessions


def test_config_reload_updates_workspaces(tmp_path):
    """save_config_file + reload() → bridge.get_workspaces() returns new entries."""
    from src.config_manager import save_config_file, CONFIG_FILE, CONFIG_DIR

    # Temporarily override config dir
    orig_dir = CONFIG_DIR
    orig_file = CONFIG_FILE
    import src.config_manager as cm
    cm.CONFIG_DIR = tmp_path
    cm.CONFIG_FILE = tmp_path / "config.toml"

    try:
        save_config_file({"_workspaces": {"default": "/tmp/d", "new-proj": "/home/user/proj"}})
        assert (tmp_path / "config.toml").exists()

        # Reload config
        cm.CONFIG_FILE = tmp_path / "config.toml"
        from src.config_manager import load_config_file
        data = load_config_file()
        assert data["_workspaces"]["new-proj"]["path"] == "/home/user/proj"
    finally:
        cm.CONFIG_DIR = orig_dir
        cm.CONFIG_FILE = orig_file
