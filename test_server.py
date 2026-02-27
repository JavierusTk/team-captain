"""Tests for Team Captain MCP Server."""

import json
import subprocess
from unittest.mock import patch, MagicMock

import pytest

import server
from server import (
    strip_ansi,
    _error,
    tmux,
    tmux_remote,
    tmux_for,
    tmux_session_alive,
    ssh_check,
    _dispatch,
    call_tool,
    list_tools,
    sessions,
    SUBPROCESS_TIMEOUT,
    SSH_OPTS,
    MSYS2_BASH,
)


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clear_sessions():
    """Ensure sessions dict is clean before and after each test."""
    sessions.clear()
    yield
    sessions.clear()


def _mock_run(stdout="", stderr="", returncode=0):
    """Create a mock subprocess.CompletedProcess."""
    m = MagicMock(spec=subprocess.CompletedProcess)
    m.stdout = stdout
    m.stderr = stderr
    m.returncode = returncode
    return m


# ── strip_ansi ───────────────────────────────────────────────────────────────

class TestStripAnsi:
    def test_plain_text_unchanged(self):
        assert strip_ansi("hello world") == "hello world"

    def test_removes_color_codes(self):
        assert strip_ansi("\x1b[31mred\x1b[0m") == "red"

    def test_removes_bold(self):
        assert strip_ansi("\x1b[1mbold\x1b[0m") == "bold"

    def test_removes_cursor_movement(self):
        assert strip_ansi("\x1b[2Jcleared\x1b[H") == "cleared"

    def test_removes_mode_switches(self):
        assert strip_ansi("\x1b(B\x1b=text\x1b[?25h") == "text"

    def test_empty_string(self):
        assert strip_ansi("") == ""

    def test_mixed_content(self):
        raw = "\x1b[32m$ \x1b[0mecho hello\r\nhello"
        assert strip_ansi(raw) == "$ echo hello\r\nhello"


# ── _error ───────────────────────────────────────────────────────────────────

class TestErrorHelper:
    def test_returns_text_content_list(self):
        result = _error("something broke", "TEST_ERROR")
        assert len(result) == 1
        assert result[0].type == "text"

    def test_json_structure(self):
        result = _error("msg", "CODE")
        data = json.loads(result[0].text)
        assert data == {"error": "msg", "code": "CODE"}

    def test_all_error_codes(self):
        for code in ("TIMEOUT", "SSH_UNREACHABLE", "INTERNAL_ERROR", "UNKNOWN_TOOL"):
            result = _error("test", code)
            data = json.loads(result[0].text)
            assert data["code"] == code


# ── tmux() ───────────────────────────────────────────────────────────────────

class TestTmux:
    @patch("server.subprocess.run")
    def test_calls_tmux_with_args(self, mock_run):
        mock_run.return_value = _mock_run(stdout="output\n")
        result = tmux(["list-sessions"])
        mock_run.assert_called_once_with(
            ["tmux", "list-sessions"],
            capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT,
        )
        assert result == "output"

    @patch("server.subprocess.run")
    def test_strips_whitespace(self, mock_run):
        mock_run.return_value = _mock_run(stdout="  result  \n")
        assert tmux(["ls"]) == "result"

    @patch("server.subprocess.run")
    def test_timeout_propagates(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="tmux", timeout=30)
        with pytest.raises(subprocess.TimeoutExpired):
            tmux(["list-sessions"])


# ── tmux_remote() ────────────────────────────────────────────────────────────

class TestTmuxRemote:
    @patch("server.subprocess.run")
    def test_builds_ssh_command(self, mock_run):
        mock_run.return_value = _mock_run(stdout="ok")
        tmux_remote("myhost", ["list-sessions"])
        args = mock_run.call_args[0][0]
        assert args[0] == "ssh"
        assert "myhost" in args
        # Should contain MSYS2 bash path in the remote command
        remote_cmd = args[-1]
        assert MSYS2_BASH in remote_cmd
        assert "tmux" in remote_cmd
        assert "list-sessions" in remote_cmd

    @patch("server.subprocess.run")
    def test_includes_ssh_opts(self, mock_run):
        mock_run.return_value = _mock_run(stdout="")
        tmux_remote("host", ["ls"])
        args = mock_run.call_args[0][0]
        for opt in SSH_OPTS:
            assert opt in args

    @patch("server.subprocess.run")
    def test_escapes_double_quotes(self, mock_run):
        mock_run.return_value = _mock_run(stdout="")
        tmux_remote("host", ["send-keys", '-l', 'echo "hi"'])
        remote_cmd = mock_run.call_args[0][0][-1]
        # Double quotes in args should be escaped for cmd.exe
        assert '\\"' in remote_cmd

    @patch("server.subprocess.run")
    def test_timeout_propagates(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ssh", timeout=30)
        with pytest.raises(subprocess.TimeoutExpired):
            tmux_remote("host", ["ls"])


# ── tmux_for() ───────────────────────────────────────────────────────────────

class TestTmuxFor:
    @patch("server.tmux")
    def test_dispatches_local_when_no_host(self, mock_tmux):
        mock_tmux.return_value = "local_output"
        sessions["test"] = {"command": "bash", "created_at": "now", "host": None}
        result = tmux_for("test", ["list-sessions"])
        mock_tmux.assert_called_once_with(["list-sessions"])
        assert result == "local_output"

    @patch("server.tmux_remote")
    def test_dispatches_remote_when_host_set(self, mock_remote):
        mock_remote.return_value = "remote_output"
        sessions["test"] = {"command": "cmd.exe", "created_at": "now", "host": "myhost"}
        result = tmux_for("test", ["list-sessions"])
        mock_remote.assert_called_once_with("myhost", ["list-sessions"])
        assert result == "remote_output"

    @patch("server.tmux")
    def test_defaults_to_local_for_unknown_session(self, mock_tmux):
        mock_tmux.return_value = ""
        tmux_for("unknown_session", ["has-session"])
        mock_tmux.assert_called_once()


# ── tmux_session_alive() ────────────────────────────────────────────────────

class TestTmuxSessionAlive:
    @patch("server.subprocess.run")
    def test_local_session_alive(self, mock_run):
        mock_run.return_value = _mock_run(returncode=0)
        sessions["s1"] = {"command": "bash", "created_at": "now", "host": None}
        assert tmux_session_alive("s1") is True

    @patch("server.subprocess.run")
    def test_local_session_dead(self, mock_run):
        mock_run.return_value = _mock_run(returncode=1)
        sessions["s1"] = {"command": "bash", "created_at": "now", "host": None}
        assert tmux_session_alive("s1") is False

    @patch("server.subprocess.run")
    def test_remote_session_alive(self, mock_run):
        mock_run.return_value = _mock_run(returncode=0)
        sessions["s1"] = {"command": "cmd", "created_at": "now", "host": "myhost"}
        assert tmux_session_alive("s1") is True

    @patch("server.subprocess.run")
    def test_returns_false_on_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="tmux", timeout=10)
        sessions["s1"] = {"command": "bash", "created_at": "now", "host": None}
        assert tmux_session_alive("s1") is False

    @patch("server.subprocess.run")
    def test_unknown_session_checks_local(self, mock_run):
        mock_run.return_value = _mock_run(returncode=1)
        assert tmux_session_alive("nonexistent") is False
        args = mock_run.call_args[0][0]
        assert args[0] == "tmux"


# ── ssh_check() ──────────────────────────────────────────────────────────────

class TestSshCheck:
    @patch("server.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = _mock_run(stdout="ok\n", returncode=0)
        ok, detail = ssh_check("myhost")
        assert ok is True
        assert detail == "ok"

    @patch("server.subprocess.run")
    def test_failure_with_stderr(self, mock_run):
        mock_run.return_value = _mock_run(stderr="Connection refused", returncode=255)
        ok, detail = ssh_check("myhost")
        assert ok is False
        assert "Connection refused" in detail

    @patch("server.subprocess.run")
    def test_failure_without_stderr(self, mock_run):
        mock_run.return_value = _mock_run(stderr="", returncode=1)
        ok, detail = ssh_check("myhost")
        assert ok is False
        assert "exit code 1" in detail

    @patch("server.subprocess.run")
    def test_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ssh", timeout=10)
        ok, detail = ssh_check("badhost")
        assert ok is False
        assert "timed out" in detail


# ── call_tool() error wrapping ───────────────────────────────────────────────

class TestCallToolErrorWrapping:
    @pytest.mark.asyncio
    @patch("server._dispatch")
    async def test_catches_timeout(self, mock_dispatch):
        mock_dispatch.side_effect = subprocess.TimeoutExpired(cmd="tmux", timeout=30)
        result = await call_tool("terminal_read", {"session_id": "s1"})
        data = json.loads(result[0].text)
        assert data["code"] == "TIMEOUT"
        assert "30" in data["error"]

    @pytest.mark.asyncio
    @patch("server._dispatch")
    async def test_catches_generic_exception(self, mock_dispatch):
        mock_dispatch.side_effect = RuntimeError("something broke")
        result = await call_tool("terminal_read", {"session_id": "s1"})
        data = json.loads(result[0].text)
        assert data["code"] == "INTERNAL_ERROR"
        assert "something broke" in data["error"]

    @pytest.mark.asyncio
    @patch("server._dispatch")
    async def test_returns_dispatch_result_on_success(self, mock_dispatch):
        mock_dispatch.return_value = [MagicMock(text='{"status": "ok"}')]
        result = await call_tool("terminal_read", {"session_id": "s1"})
        assert result == mock_dispatch.return_value


# ── _dispatch: terminal_create ───────────────────────────────────────────────

class TestDispatchCreate:
    @pytest.mark.asyncio
    @patch("server.tmux")
    async def test_local_session_created(self, mock_tmux):
        mock_tmux.return_value = ""
        result = await _dispatch("terminal_create", {"command": "bash"})
        data = json.loads(result[0].text)
        assert data["status"] == "created"
        assert data["session_id"].startswith("t_")
        assert data["session_id"] in sessions
        assert sessions[data["session_id"]]["command"] == "bash"
        assert sessions[data["session_id"]]["host"] is None

    @pytest.mark.asyncio
    @patch("server.tmux")
    async def test_custom_session_name(self, mock_tmux):
        mock_tmux.return_value = ""
        result = await _dispatch("terminal_create", {
            "command": "bash",
            "session_name": "worker1",
        })
        data = json.loads(result[0].text)
        assert data["session_id"] == "worker1"
        assert "worker1" in sessions

    @pytest.mark.asyncio
    @patch("server.tmux")
    async def test_passes_cols_rows(self, mock_tmux):
        mock_tmux.return_value = ""
        await _dispatch("terminal_create", {
            "command": "bash", "cols": 100, "rows": 25,
        })
        args = mock_tmux.call_args[0][0]
        assert "100" in args
        assert "25" in args

    @pytest.mark.asyncio
    @patch("server.tmux_remote")
    @patch("server.ssh_check", return_value=(True, "ok"))
    async def test_remote_session_created(self, mock_check, mock_remote):
        mock_remote.return_value = ""
        result = await _dispatch("terminal_create", {
            "command": "cmd.exe", "host": "myhost",
        })
        data = json.loads(result[0].text)
        assert data["status"] == "created"
        mock_check.assert_called_once_with("myhost")
        mock_remote.assert_called_once()
        sid = data["session_id"]
        assert sessions[sid]["host"] == "myhost"

    @pytest.mark.asyncio
    @patch("server.ssh_check", return_value=(False, "Connection refused"))
    async def test_ssh_check_fails(self, mock_check):
        result = await _dispatch("terminal_create", {
            "command": "cmd.exe", "host": "badhost",
        })
        data = json.loads(result[0].text)
        assert data["code"] == "SSH_UNREACHABLE"
        assert "badhost" in data["error"]
        assert len(sessions) == 0  # no session created


# ── _dispatch: terminal_read ────────────────────────────────────────────────

class TestDispatchRead:
    @pytest.mark.asyncio
    @patch("server.tmux_for")
    async def test_reads_output(self, mock_tmux_for):
        mock_tmux_for.return_value = "line1\nline2\nline3"
        sessions["s1"] = {"command": "bash", "created_at": "now", "host": None}
        result = await _dispatch("terminal_read", {"session_id": "s1"})
        assert result[0].text == "line1\nline2\nline3"

    @pytest.mark.asyncio
    @patch("server.tmux_for")
    async def test_strips_ansi_by_default(self, mock_tmux_for):
        mock_tmux_for.return_value = "\x1b[31mred\x1b[0m"
        sessions["s1"] = {"command": "bash", "created_at": "now", "host": None}
        result = await _dispatch("terminal_read", {"session_id": "s1"})
        assert result[0].text == "red"

    @pytest.mark.asyncio
    @patch("server.tmux_for")
    async def test_preserves_ansi_when_disabled(self, mock_tmux_for):
        raw = "\x1b[31mred\x1b[0m"
        mock_tmux_for.return_value = raw
        sessions["s1"] = {"command": "bash", "created_at": "now", "host": None}
        result = await _dispatch("terminal_read", {
            "session_id": "s1", "strip_ansi": False,
        })
        assert result[0].text == raw

    @pytest.mark.asyncio
    @patch("server.tmux_for")
    async def test_custom_line_count(self, mock_tmux_for):
        mock_tmux_for.return_value = ""
        sessions["s1"] = {"command": "bash", "created_at": "now", "host": None}
        await _dispatch("terminal_read", {"session_id": "s1", "lines": 10})
        args = mock_tmux_for.call_args[0][1]
        assert "-10" in args


# ── _dispatch: terminal_send ────────────────────────────────────────────────

class TestDispatchSend:
    @pytest.mark.asyncio
    @patch("server.tmux_for")
    async def test_send_text_with_enter(self, mock_tmux_for):
        mock_tmux_for.return_value = ""
        sessions["s1"] = {"command": "bash", "created_at": "now", "host": None}
        result = await _dispatch("terminal_send", {
            "session_id": "s1", "text": "ls -la",
        })
        data = json.loads(result[0].text)
        assert data["status"] == "sent"
        # Should be called twice: once for text (-l), once for Enter
        assert mock_tmux_for.call_count == 2

    @pytest.mark.asyncio
    @patch("server.tmux_for")
    async def test_send_text_without_enter(self, mock_tmux_for):
        mock_tmux_for.return_value = ""
        sessions["s1"] = {"command": "bash", "created_at": "now", "host": None}
        await _dispatch("terminal_send", {
            "session_id": "s1", "text": "partial", "press_enter": False,
        })
        # Only one call for text, no Enter
        assert mock_tmux_for.call_count == 1
        args = mock_tmux_for.call_args[0][1]
        assert "-l" in args
        assert "partial" in args

    @pytest.mark.asyncio
    @patch("server.tmux_for")
    async def test_send_special_key(self, mock_tmux_for):
        mock_tmux_for.return_value = ""
        sessions["s1"] = {"command": "bash", "created_at": "now", "host": None}
        await _dispatch("terminal_send", {
            "session_id": "s1", "text": "", "special_key": "C-c",
        })
        args = mock_tmux_for.call_args[0][1]
        assert "C-c" in args
        # Special key path should not use -l
        assert "-l" not in args

    @pytest.mark.asyncio
    @patch("server.tmux_for")
    async def test_send_empty_text_only_enter(self, mock_tmux_for):
        mock_tmux_for.return_value = ""
        sessions["s1"] = {"command": "bash", "created_at": "now", "host": None}
        await _dispatch("terminal_send", {
            "session_id": "s1", "text": "",
        })
        # Only Enter sent, no -l call for empty text
        assert mock_tmux_for.call_count == 1
        args = mock_tmux_for.call_args[0][1]
        assert "Enter" in args


# ── _dispatch: terminal_send_raw ─────────────────────────────────────────────

class TestDispatchSendRaw:
    @pytest.mark.asyncio
    @patch("server.tmux_for")
    async def test_sends_raw_keys(self, mock_tmux_for):
        mock_tmux_for.return_value = ""
        sessions["s1"] = {"command": "bash", "created_at": "now", "host": None}
        result = await _dispatch("terminal_send_raw", {
            "session_id": "s1", "keys": "Up",
        })
        data = json.loads(result[0].text)
        assert data["status"] == "sent"
        args = mock_tmux_for.call_args[0][1]
        assert "Up" in args
        assert "-l" not in args  # raw keys should NOT use -l


# ── _dispatch: terminal_wait ────────────────────────────────────────────────

class TestDispatchWait:
    @pytest.mark.asyncio
    @patch("server.tmux_for")
    async def test_found_immediately(self, mock_tmux_for):
        mock_tmux_for.return_value = "some output with READY marker"
        sessions["s1"] = {"command": "bash", "created_at": "now", "host": None}
        result = await _dispatch("terminal_wait", {
            "session_id": "s1", "pattern": "READY",
        })
        data = json.loads(result[0].text)
        assert data["found"] is True
        assert data["elapsed"] >= 0
        assert "READY" in data["output"]

    @pytest.mark.asyncio
    @patch("server.tmux_for")
    async def test_timeout_when_not_found(self, mock_tmux_for):
        mock_tmux_for.return_value = "no match here"
        sessions["s1"] = {"command": "bash", "created_at": "now", "host": None}
        result = await _dispatch("terminal_wait", {
            "session_id": "s1",
            "pattern": "NEVER",
            "timeout_seconds": 1,
            "poll_interval": 0.3,
        })
        data = json.loads(result[0].text)
        assert data["found"] is False
        assert data["elapsed"] == 1

    @pytest.mark.asyncio
    @patch("server.tmux_for")
    async def test_found_after_delay(self, mock_tmux_for):
        call_count = 0
        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                return "output with DONE"
            return "still waiting..."
        mock_tmux_for.side_effect = side_effect
        sessions["s1"] = {"command": "bash", "created_at": "now", "host": None}
        result = await _dispatch("terminal_wait", {
            "session_id": "s1",
            "pattern": "DONE",
            "timeout_seconds": 10,
            "poll_interval": 0.1,
        })
        data = json.loads(result[0].text)
        assert data["found"] is True
        assert call_count >= 3


# ── _dispatch: terminal_close ───────────────────────────────────────────────

class TestDispatchClose:
    @pytest.mark.asyncio
    @patch("server.tmux_for")
    async def test_kills_and_removes(self, mock_tmux_for):
        mock_tmux_for.return_value = ""
        sessions["s1"] = {"command": "bash", "created_at": "now", "host": None}
        result = await _dispatch("terminal_close", {"session_id": "s1"})
        data = json.loads(result[0].text)
        assert data["status"] == "closed"
        assert "s1" not in sessions

    @pytest.mark.asyncio
    @patch("server.tmux_for")
    async def test_close_untracked_session(self, mock_tmux_for):
        mock_tmux_for.return_value = ""
        # Closing a session not in sessions dict should not crash
        result = await _dispatch("terminal_close", {"session_id": "orphan"})
        data = json.loads(result[0].text)
        assert data["status"] == "closed"


# ── _dispatch: terminal_list ────────────────────────────────────────────────

class TestDispatchList:
    @pytest.mark.asyncio
    @patch("server.tmux")
    async def test_empty_list(self, mock_tmux):
        mock_tmux.return_value = ""
        result = await _dispatch("terminal_list", {})
        data = json.loads(result[0].text)
        assert data == []

    @pytest.mark.asyncio
    @patch("server.tmux")
    async def test_tracked_alive_session(self, mock_tmux):
        mock_tmux.return_value = "s1"
        sessions["s1"] = {"command": "bash", "created_at": "2024-01-01", "host": None}
        result = await _dispatch("terminal_list", {})
        data = json.loads(result[0].text)
        assert len(data) == 1
        assert data[0]["session_id"] == "s1"
        assert data[0]["alive"] is True
        assert data[0]["command"] == "bash"

    @pytest.mark.asyncio
    @patch("server.tmux")
    async def test_tracked_dead_session(self, mock_tmux):
        mock_tmux.return_value = ""  # no sessions alive in tmux
        sessions["s1"] = {"command": "bash", "created_at": "2024-01-01", "host": None}
        result = await _dispatch("terminal_list", {})
        data = json.loads(result[0].text)
        assert len(data) == 1
        assert data[0]["alive"] is False

    @pytest.mark.asyncio
    @patch("server.tmux")
    async def test_orphan_session(self, mock_tmux):
        mock_tmux.return_value = "orphan_sess"
        # Not tracked in sessions dict
        result = await _dispatch("terminal_list", {})
        data = json.loads(result[0].text)
        assert len(data) == 1
        assert data[0]["session_id"] == "orphan_sess"
        assert data[0]["alive"] is True
        assert data[0]["command"] == "?"

    @pytest.mark.asyncio
    @patch("server.tmux_remote")
    @patch("server.tmux")
    async def test_mixed_local_and_remote(self, mock_tmux, mock_remote):
        mock_tmux.return_value = "local1"
        mock_remote.return_value = "remote1"
        sessions["local1"] = {"command": "bash", "created_at": "now", "host": None}
        sessions["remote1"] = {"command": "cmd", "created_at": "now", "host": "winhost"}
        result = await _dispatch("terminal_list", {})
        data = json.loads(result[0].text)
        assert len(data) == 2
        by_id = {d["session_id"]: d for d in data}
        assert by_id["local1"]["alive"] is True
        assert by_id["remote1"]["alive"] is True


# ── _dispatch: terminal_resize ──────────────────────────────────────────────

class TestDispatchResize:
    @pytest.mark.asyncio
    @patch("server.tmux_for")
    async def test_resizes_session(self, mock_tmux_for):
        mock_tmux_for.return_value = ""
        sessions["s1"] = {"command": "bash", "created_at": "now", "host": None}
        result = await _dispatch("terminal_resize", {
            "session_id": "s1", "cols": 120, "rows": 40,
        })
        data = json.loads(result[0].text)
        assert data["status"] == "resized"
        args = mock_tmux_for.call_args[0][1]
        assert "120" in args
        assert "40" in args

    @pytest.mark.asyncio
    @patch("server.tmux_for")
    async def test_default_dimensions(self, mock_tmux_for):
        mock_tmux_for.return_value = ""
        sessions["s1"] = {"command": "bash", "created_at": "now", "host": None}
        await _dispatch("terminal_resize", {"session_id": "s1"})
        args = mock_tmux_for.call_args[0][1]
        assert "220" in args
        assert "50" in args


# ── _dispatch: terminal_cleanup ─────────────────────────────────────────────

class TestDispatchCleanup:
    @pytest.mark.asyncio
    @patch("server.tmux_session_alive")
    async def test_removes_dead_sessions(self, mock_alive):
        sessions["alive1"] = {"command": "bash", "created_at": "now", "host": None}
        sessions["dead1"] = {"command": "bash", "created_at": "now", "host": None}
        sessions["dead2"] = {"command": "bash", "created_at": "now", "host": None}
        mock_alive.side_effect = lambda sid: sid == "alive1"
        result = await _dispatch("terminal_cleanup", {})
        data = json.loads(result[0].text)
        assert data["removed"] == 2
        assert set(data["removed_ids"]) == {"dead1", "dead2"}
        assert data["remaining"] == 1
        assert "alive1" in sessions
        assert "dead1" not in sessions
        assert "dead2" not in sessions

    @pytest.mark.asyncio
    @patch("server.tmux_session_alive")
    async def test_nothing_to_clean(self, mock_alive):
        sessions["s1"] = {"command": "bash", "created_at": "now", "host": None}
        mock_alive.return_value = True
        result = await _dispatch("terminal_cleanup", {})
        data = json.loads(result[0].text)
        assert data["removed"] == 0
        assert data["removed_ids"] == []
        assert data["remaining"] == 1

    @pytest.mark.asyncio
    async def test_empty_sessions(self):
        result = await _dispatch("terminal_cleanup", {})
        data = json.loads(result[0].text)
        assert data["removed"] == 0
        assert data["remaining"] == 0


# ── _dispatch: unknown tool ─────────────────────────────────────────────────

class TestDispatchUnknown:
    @pytest.mark.asyncio
    async def test_unknown_tool(self):
        result = await _dispatch("nonexistent_tool", {})
        data = json.loads(result[0].text)
        assert data["code"] == "UNKNOWN_TOOL"
        assert "nonexistent_tool" in data["error"]


# ── list_tools ───────────────────────────────────────────────────────────────

class TestListTools:
    @pytest.mark.asyncio
    async def test_returns_all_tools(self):
        tools = await list_tools()
        names = {t.name for t in tools}
        expected = {
            "terminal_create", "terminal_read", "terminal_send",
            "terminal_send_raw", "terminal_wait", "terminal_close",
            "terminal_list", "terminal_resize", "terminal_cleanup",
        }
        assert names == expected

    @pytest.mark.asyncio
    async def test_all_tools_have_schemas(self):
        tools = await list_tools()
        for tool in tools:
            assert tool.inputSchema is not None
            assert tool.inputSchema["type"] == "object"

    @pytest.mark.asyncio
    async def test_required_fields_present(self):
        tools = await list_tools()
        by_name = {t.name: t for t in tools}
        # terminal_create requires "command"
        assert "command" in by_name["terminal_create"].inputSchema["required"]
        # terminal_read requires "session_id"
        assert "session_id" in by_name["terminal_read"].inputSchema["required"]
        # terminal_list and terminal_cleanup have no required fields
        assert "required" not in by_name["terminal_list"].inputSchema
        assert "required" not in by_name["terminal_cleanup"].inputSchema
