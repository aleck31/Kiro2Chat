"""Tests for ACP client."""

import json
import threading
from unittest.mock import MagicMock

from src.acp.client import ACPClient, PromptResult


class FakeProc:
    """Fake subprocess for testing JSON-RPC transport."""

    def __init__(self):
        self.stdin = MagicMock()
        self.stderr = MagicMock()
        self._lines = []
        self._lock = threading.Lock()

    def queue_response(self, msg: dict):
        self._lines.append(json.dumps(msg).encode() + b"\n")

    def poll(self):
        return None

    @property
    def pid(self):
        return 12345

    @property
    def stdout(self):
        return self

    def readline(self, _bufsize=0):
        with self._lock:
            if self._lines:
                return self._lines.pop(0)
        return b""


def test_detect_image_mime():
    assert ACPClient._detect_image_mime("iVBORw") == "image/png"
    assert ACPClient._detect_image_mime("/9j/") == "image/jpeg"
    assert ACPClient._detect_image_mime("R0lGOD") == "image/gif"
    assert ACPClient._detect_image_mime("UklGR") == "image/webp"
    assert ACPClient._detect_image_mime("unknown") is None


def test_build_prompt_result():
    client = ACPClient()
    sid = "test-session"
    client._session_updates[sid] = [
        {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Hello"}},
        {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": " world"}},
        {"sessionUpdate": "tool_call", "toolCallId": "tc1", "title": "Running: ls", "kind": "execute", "status": "pending"},
        {"sessionUpdate": "tool_call_update", "toolCallId": "tc1", "status": "completed"},
    ]
    result = client._build_prompt_result(sid, {"stopReason": "end_turn"})
    assert isinstance(result, PromptResult)
    assert result.text == "Hello world"
    assert result.stop_reason == "end_turn"
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].tool_call_id == "tc1"
    assert result.tool_calls[0].status == "completed"


def test_build_prompt_result_empty():
    client = ACPClient()
    client._session_updates["s1"] = []
    result = client._build_prompt_result("s1", {})
    assert result.text == ""
    assert result.tool_calls == []
    assert result.image_paths == []


def test_build_prompt_result_image_paths():
    client = ACPClient()
    sid = "img-session"
    client._session_updates[sid] = [
        {"sessionUpdate": "tool_call", "toolCallId": "tc1", "title": "Generate image", "kind": "tool", "status": "pending"},
        {"sessionUpdate": "tool_call_update", "toolCallId": "tc1", "status": "completed", "content": [
            {"content": {"type": "image", "path": "/tmp/out.png"}},
            {"content": {"type": "text", "text": "Image generated"}},
            {"content": {"type": "image", "path": "/tmp/out2.jpg"}},
        ]},
    ]
    result = client._build_prompt_result(sid, {"stopReason": "end_turn"})
    assert result.image_paths == ["/tmp/out.png", "/tmp/out2.jpg"]
    assert result.tool_calls[0].image_paths == ["/tmp/out.png", "/tmp/out2.jpg"]
    assert result.tool_calls[0].content == "Image generated"


def test_permission_auto_approve_no_handler():
    """Without a handler, permissions are auto-approved."""
    client = ACPClient()
    client._proc = MagicMock()
    client._proc.stdin = MagicMock()
    client._handle_permission_request(1, {
        "sessionId": "s1",
        "toolCall": {"toolCallId": "tc1", "title": "test"},
        "options": [],
    })
    # Should have sent allow_once response
    written = client._proc.stdin.write.call_args[0][0]
    msg = json.loads(written.decode())
    assert msg["result"]["outcome"]["optionId"] == "allow_once"


def test_handle_line_response():
    """Test that responses resolve pending requests."""
    client = ACPClient()
    evt = threading.Event()
    holder = []
    client._pending[42] = (evt, holder)

    client._handle_line(json.dumps({"jsonrpc": "2.0", "id": 42, "result": {"foo": "bar"}}))

    assert evt.is_set()
    assert holder[0] == {"foo": "bar"}


def test_handle_line_error():
    client = ACPClient()
    evt = threading.Event()
    holder = []
    client._pending[7] = (evt, holder)

    client._handle_line(json.dumps({"jsonrpc": "2.0", "id": 7, "error": {"code": -1, "message": "fail"}}))

    assert evt.is_set()
    assert holder[0] is None
    assert holder[1]["message"] == "fail"


def test_stream_callback():
    client = ACPClient()
    sid = "s1"
    client._session_updates[sid] = []
    chunks = []
    client._stream_callbacks[sid] = lambda c, a: chunks.append(c)
    client._stream_accum[sid] = []

    client._handle_session_update(sid, {
        "sessionUpdate": "agent_message_chunk",
        "content": {"type": "text", "text": "hi"},
    })
    assert chunks == ["hi"]
    assert client._stream_accum[sid] == ["hi"]
