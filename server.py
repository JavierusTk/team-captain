#!/usr/bin/env python3
"""Team Captain MCP Server — orchestrate multi-agent terminal sessions via tmux."""

import asyncio
import json
import logging
import re
import shlex
import subprocess
import sys
import time
import uuid
from datetime import datetime

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

# ── logging (stderr only — stdout is reserved for MCP protocol) ──────────────

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("team-captain")

app = Server("terminal-mcp")
sessions: dict[str, dict] = {}  # session_id → {command, created_at, host}

MSYS2_BASH = "C:/msys64/usr/bin/bash.exe"
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
SUBPROCESS_TIMEOUT = 30  # seconds — prevents hung SSH from blocking forever


# ── error helper ─────────────────────────────────────────────────────────────

def _error(message: str, code: str) -> list[types.TextContent]:
    return [types.TextContent(type="text", text=json.dumps({"error": message, "code": code}))]


# ── tmux helpers ─────────────────────────────────────────────────────────────

def tmux(args: list[str]) -> str:
    result = subprocess.run(
        ["tmux"] + args,
        capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT,
    )
    return result.stdout.strip()


def tmux_remote(host: str, args: list[str]) -> str:
    """Run a tmux command on a Windows host via SSH + MSYS2 bash."""
    bash_cmd = " ".join(shlex.quote(str(a)) for a in ["tmux"] + args)
    # Escape double quotes so cmd.exe passes them through to bash -lc "..."
    bash_cmd_escaped = bash_cmd.replace('"', '\\"')
    remote_cmd = f'{MSYS2_BASH} -lc "{bash_cmd_escaped}"'
    result = subprocess.run(
        ["ssh"] + SSH_OPTS + [host, remote_cmd],
        capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT,
    )
    return result.stdout.strip()


def tmux_for(session_id: str, args: list[str]) -> str:
    """Dispatch tmux command to local or remote host based on session metadata."""
    host = sessions.get(session_id, {}).get("host")
    if host:
        return tmux_remote(host, args)
    return tmux(args)


def tmux_session_alive(session_id: str) -> bool:
    host = sessions.get(session_id, {}).get("host")
    try:
        if host:
            bash_cmd = f"tmux has-session -t {shlex.quote(session_id)}"
            result = subprocess.run(
                ["ssh"] + SSH_OPTS + [host, f'{MSYS2_BASH} -lc "{bash_cmd}"'],
                capture_output=True, timeout=10,
            )
        else:
            result = subprocess.run(
                ["tmux", "has-session", "-t", session_id],
                capture_output=True, timeout=10,
            )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False


def ssh_check(host: str) -> tuple[bool, str]:
    """Verify SSH connectivity before creating a remote session."""
    try:
        result = subprocess.run(
            ["ssh"] + SSH_OPTS + [host, "echo", "ok"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return True, "ok"
        return False, result.stderr.strip() or f"exit code {result.returncode}"
    except subprocess.TimeoutExpired:
        return False, "connection timed out"


def strip_ansi(text: str) -> str:
    return re.sub(r'\x1b\[[0-9;]*[mKHABCDJn]|\x1b\(B|\x1b=|\x1b\[\?[0-9]+[hl]', '', text)


# ── tool definitions ──────────────────────────────────────────────────────────

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="terminal_create",
            description="Create a tmux session and run a command in it.",
            inputSchema={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Command to run"},
                    "session_name": {"type": "string", "description": "Optional session name; auto-generated if omitted"},
                    "cols": {"type": "integer", "default": 220, "description": "Terminal width"},
                    "rows": {"type": "integer", "default": 50, "description": "Terminal height"},
                    "host": {"type": "string", "description": "SSH host for Windows sessions (e.g. 'localhost'). Omit for local WSL session."},
                },
                "required": ["command"],
            },
        ),
        types.Tool(
            name="terminal_read",
            description="Capture current terminal output (last N lines).",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "lines": {"type": "integer", "default": 50, "description": "Number of lines to capture from the end"},
                    "strip_ansi": {"type": "boolean", "default": True, "description": "Strip ANSI escape codes"},
                },
                "required": ["session_id"],
            },
        ),
        types.Tool(
            name="terminal_send",
            description="Send text to a session. Uses tmux -l internally for safe literal input.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "text": {"type": "string", "description": "Text to send"},
                    "press_enter": {"type": "boolean", "default": True, "description": "Append Enter after the text"},
                    "special_key": {"type": "string", "description": "Alternative: send a special key (Up, Down, Space, Escape, Tab, C-c)"},
                },
                "required": ["session_id", "text"],
            },
        ),
        types.Tool(
            name="terminal_send_raw",
            description="Send raw key sequences in tmux notation.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "keys": {"type": "string", "description": "tmux key notation: Up, Down, Space, Enter, C-c"},
                },
                "required": ["session_id", "keys"],
            },
        ),
        types.Tool(
            name="terminal_wait",
            description="Wait until session output contains a pattern, or until timeout.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "pattern": {"type": "string", "description": "Text to wait for in the output"},
                    "timeout_seconds": {"type": "integer", "default": 30},
                    "poll_interval": {"type": "number", "default": 0.5},
                },
                "required": ["session_id", "pattern"],
            },
        ),
        types.Tool(
            name="terminal_close",
            description="Kill a tmux session.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                },
                "required": ["session_id"],
            },
        ),
        types.Tool(
            name="terminal_list",
            description="List active sessions. Queries tmux directly as source of truth.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="terminal_resize",
            description="Resize the window of an active session.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "cols": {"type": "integer", "default": 220},
                    "rows": {"type": "integer", "default": 50},
                },
                "required": ["session_id"],
            },
        ),
        types.Tool(
            name="terminal_cleanup",
            description="Remove dead sessions from tracking. Returns the IDs that were cleaned up.",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


# ── tool handlers ─────────────────────────────────────────────────────────────

@app.call_tool()
async def call_tool(name: str, arguments: dict):
    try:
        return await _dispatch(name, arguments)
    except subprocess.TimeoutExpired as e:
        log.error("%s timed out after %ss", name, e.timeout)
        return _error(f"Command timed out after {e.timeout}s", "TIMEOUT")
    except Exception as e:
        log.error("%s failed: %s", name, e, exc_info=True)
        return _error(str(e), "INTERNAL_ERROR")


async def _dispatch(name: str, arguments: dict):
    if name == "terminal_create":
        session_id = arguments.get("session_name") or f"t_{uuid.uuid4().hex[:8]}"
        cols = arguments.get("cols", 220)
        rows = arguments.get("rows", 50)
        cmd = arguments["command"]
        host = arguments.get("host") or None

        # SSH health check before remote session creation
        if host:
            ok, detail = ssh_check(host)
            if not ok:
                log.warning("SSH check failed for %s: %s", host, detail)
                return _error(f"Cannot reach host '{host}' via SSH: {detail}", "SSH_UNREACHABLE")

        start = time.monotonic()
        if host:
            tmux_remote(host, ["new-session", "-d", "-s", session_id, "-x", str(cols), "-y", str(rows), cmd])
        else:
            tmux(["new-session", "-d", "-s", session_id, "-x", str(cols), "-y", str(rows), cmd])
        duration_ms = round((time.monotonic() - start) * 1000)

        sessions[session_id] = {
            "command": cmd,
            "created_at": datetime.now().isoformat(),
            "host": host,
        }
        log.info("create %s (%dms)%s", session_id, duration_ms, f" on {host}" if host else "")
        return [types.TextContent(type="text", text=json.dumps({"session_id": session_id, "status": "created"}))]

    elif name == "terminal_read":
        session_id = arguments["session_id"]
        lines = arguments.get("lines", 50)
        output = tmux_for(session_id, ["capture-pane", "-t", session_id, "-p", "-S", f"-{lines}"])
        if arguments.get("strip_ansi", True):
            output = strip_ansi(output)
        log.info("read %s (%d lines)", session_id, lines)
        return [types.TextContent(type="text", text=output)]

    elif name == "terminal_send":
        session_id = arguments["session_id"]
        text = arguments.get("text", "")
        press_enter = arguments.get("press_enter", True)
        if arguments.get("special_key"):
            tmux_for(session_id, ["send-keys", "-t", session_id, arguments["special_key"]])
        else:
            if text:
                tmux_for(session_id, ["send-keys", "-t", session_id, "-l", text])
            if press_enter:
                tmux_for(session_id, ["send-keys", "-t", session_id, "Enter"])
        log.info("send %s", session_id)
        return [types.TextContent(type="text", text='{"status": "sent"}')]

    elif name == "terminal_send_raw":
        session_id = arguments["session_id"]
        tmux_for(session_id, ["send-keys", "-t", session_id, arguments["keys"]])
        log.info("send_raw %s", session_id)
        return [types.TextContent(type="text", text='{"status": "sent"}')]

    elif name == "terminal_wait":
        session_id = arguments["session_id"]
        pattern = arguments["pattern"]
        timeout = arguments.get("timeout_seconds", 30)
        poll = arguments.get("poll_interval", 0.5)
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            output = strip_ansi(tmux_for(session_id, ["capture-pane", "-t", session_id, "-p", "-S", "-200"]))
            if pattern in output:
                elapsed = time.monotonic() - start
                log.info("wait %s found '%s' in %.1fs", session_id, pattern, elapsed)
                return [types.TextContent(type="text", text=json.dumps({
                    "found": True,
                    "elapsed": round(elapsed, 1),
                    "output": output,
                }))]
            await asyncio.sleep(poll)
        log.info("wait %s timeout after %ds for '%s'", session_id, timeout, pattern)
        return [types.TextContent(type="text", text=json.dumps({"found": False, "elapsed": timeout}))]

    elif name == "terminal_close":
        session_id = arguments["session_id"]
        tmux_for(session_id, ["kill-session", "-t", session_id])
        sessions.pop(session_id, None)
        log.info("close %s", session_id)
        return [types.TextContent(type="text", text='{"status": "closed"}')]

    elif name == "terminal_list":
        # Local sessions
        raw = tmux(["list-sessions", "-F", "#{session_name}"])
        alive_local = set(raw.splitlines()) if raw else set()

        # Remote sessions — query each unique host once
        alive_remote: dict[str, set] = {}
        for info in sessions.values():
            h = info.get("host")
            if h and h not in alive_remote:
                raw_r = tmux_remote(h, ["list-sessions", "-F", "#{session_name}"])
                alive_remote[h] = set(raw_r.splitlines()) if raw_r else set()

        result = []
        for sid, info in sessions.items():
            h = info.get("host")
            alive = sid in (alive_remote.get(h, set()) if h else alive_local)
            result.append({**info, "session_id": sid, "alive": alive})

        # Orphan local sessions (alive in tmux but not tracked)
        for sid in alive_local - set(sessions):
            result.append({"session_id": sid, "command": "?", "created_at": "?", "alive": True, "host": None})

        log.info("list: %d tracked, %d alive", len(sessions), sum(1 for r in result if r["alive"]))
        return [types.TextContent(type="text", text=json.dumps(result))]

    elif name == "terminal_resize":
        session_id = arguments["session_id"]
        cols = arguments.get("cols", 220)
        rows = arguments.get("rows", 50)
        tmux_for(session_id, ["resize-window", "-t", session_id, "-x", str(cols), "-y", str(rows)])
        log.info("resize %s to %dx%d", session_id, cols, rows)
        return [types.TextContent(type="text", text='{"status": "resized"}')]

    elif name == "terminal_cleanup":
        dead = [sid for sid in sessions if not tmux_session_alive(sid)]
        for sid in dead:
            sessions.pop(sid, None)
        log.info("cleanup: removed %d dead sessions %s", len(dead), dead)
        return [types.TextContent(type="text", text=json.dumps({
            "removed": len(dead),
            "removed_ids": dead,
            "remaining": len(sessions),
        }))]

    else:
        return _error(f"Unknown tool: {name}", "UNKNOWN_TOOL")


async def main():
    log.info("Team Captain MCP server starting")
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options(),
        )

if __name__ == "__main__":
    asyncio.run(main())
