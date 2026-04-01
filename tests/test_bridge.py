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
    mock_cfg.workspaces = {"default": str(tmp_path / "default")}
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
    assert b.get_sessions() == {}
    info = _SessionInfo("sess-123", workspace="default")
    b._sessions[("web.private.abc", "default")] = info
    sessions = b.get_sessions()
    assert "web.private.abc@default" in sessions
    assert sessions["web.private.abc@default"]["session_id"] == "sess-123"
    assert sessions["web.private.abc@default"]["workspace"] == "default"


def test_active_workspace_default():
    b = Bridge()
    assert b.get_active_workspace("chat1") == "default"


@patch("src.config.config")
def test_switch_workspace(mock_cfg):
    mock_cfg.workspaces = {"default": "/tmp/d", "myproj": "/tmp/p"}
    b = Bridge()
    b.switch_workspace("chat1", "myproj")
    assert b.get_active_workspace("chat1") == "myproj"


@patch("src.config.config")
def test_switch_workspace_unknown(mock_cfg):
    mock_cfg.workspaces = {"default": "/tmp/d"}
    b = Bridge()
    import pytest
    with pytest.raises(ValueError, match="Unknown workspace"):
        b.switch_workspace("chat1", "nonexistent")


def test_clear():
    b = Bridge()
    info = _SessionInfo("sess-1", workspace="default")
    b._sessions[("chat1", "default")] = info
    b.clear("chat1")
    assert ("chat1", "default") not in b._sessions


@patch("src.config.config")
def test_switch_workspace_creates_new_session_key(mock_cfg):
    """After switch, _ensure_session uses new (chat_id, workspace) key; old session survives."""
    mock_cfg.workspaces = {"default": "/tmp/d", "proj": "/tmp/p"}
    b = Bridge()
    # Fake an existing session under default
    old_info = _SessionInfo("old-sess", workspace="default")
    b._sessions[("chat1", "default")] = old_info

    b.switch_workspace("chat1", "proj")
    assert b._session_key("chat1") == ("chat1", "proj")
    # Old session still exists
    assert ("chat1", "default") in b._sessions
    assert b._sessions[("chat1", "default")].session_id == "old-sess"
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
        assert data["_workspaces"]["new-proj"] == "/home/user/proj"
    finally:
        cm.CONFIG_DIR = orig_dir
        cm.CONFIG_FILE = orig_file
