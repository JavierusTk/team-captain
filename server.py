#!/usr/bin/env python3
"""Team Captain MCP Server — orchestrate multi-agent terminal sessions via tmux."""

import asyncio
import json
import re
import shlex
import subprocess
import time
import uuid
from datetime import datetime

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

app = Server("terminal-mcp")
sessions: dict[str, dict] = {}  # session_id → {command, created_at, host}

MSYS2_BASH = "C:/msys64/usr/bin/bash.exe"
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]


# ── tmux helpers ─────────────────────────────────────────────────────────────

def tmux(args: list[str]) -> str:
    result = subprocess.run(
        ["tmux"] + args,
        capture_output=True, text=True
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
        capture_output=True, text=True
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
    if host:
        bash_cmd = f"tmux has-session -t {shlex.quote(session_id)}"
        result = subprocess.run(
            ["ssh"] + SSH_OPTS + [host, f'{MSYS2_BASH} -lc "{bash_cmd}"'],
            capture_output=True
        )
    else:
        result = subprocess.run(
            ["tmux", "has-session", "-t", session_id],
            capture_output=True
        )
    return result.returncode == 0


def strip_ansi(text: str) -> str:
    return re.sub(r'\x1b\[[0-9;]*[mKHABCDJn]|\x1b\(B|\x1b=|\x1b\[\?[0-9]+[hl]', '', text)


# ── tool definitions ──────────────────────────────────────────────────────────

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="terminal_create",
            description="Crea una sesión tmux y ejecuta un comando en ella.",
            inputSchema={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Comando a ejecutar"},
                    "session_name": {"type": "string", "description": "Nombre opcional de la sesión"},
                    "cols": {"type": "integer", "default": 220, "description": "Ancho del terminal"},
                    "rows": {"type": "integer", "default": 50, "description": "Alto del terminal"},
                    "host": {"type": "string", "description": "Host SSH remoto para sesiones Windows (ej: 'localhost'). Si se omite, sesión local en WSL."},
                },
                "required": ["command"],
            },
        ),
        types.Tool(
            name="terminal_read",
            description="Captura el output actual de la sesión (últimas N líneas).",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "lines": {"type": "integer", "default": 50, "description": "Líneas a capturar desde el final"},
                    "strip_ansi": {"type": "boolean", "default": True, "description": "Limpiar códigos ANSI"},
                },
                "required": ["session_id"],
            },
        ),
        types.Tool(
            name="terminal_send",
            description="Envía texto a la sesión. Usa -l internamente para envío literal seguro.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "text": {"type": "string", "description": "Texto a enviar"},
                    "press_enter": {"type": "boolean", "default": True, "description": "Añadir Enter al final"},
                    "special_key": {"type": "string", "description": "Alternativa: Up, Down, Space, Escape, Tab, C-c"},
                },
                "required": ["session_id", "text"],
            },
        ),
        types.Tool(
            name="terminal_send_raw",
            description="Envía secuencias de teclas crudas en notación tmux.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "keys": {"type": "string", "description": "Notación tmux: Up, Down, Space, Enter, C-c"},
                },
                "required": ["session_id", "keys"],
            },
        ),
        types.Tool(
            name="terminal_wait",
            description="Espera hasta que el output contenga un patrón (o timeout).",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "pattern": {"type": "string", "description": "Texto a esperar en el output"},
                    "timeout_seconds": {"type": "integer", "default": 30},
                    "poll_interval": {"type": "number", "default": 0.5},
                },
                "required": ["session_id", "pattern"],
            },
        ),
        types.Tool(
            name="terminal_close",
            description="Cierra la sesión tmux.",
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
            description="Lista sesiones activas. Consulta tmux directamente como fuente de verdad.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="terminal_resize",
            description="Cambia el tamaño de la ventana de una sesión activa.",
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
    ]


# ── tool handlers ─────────────────────────────────────────────────────────────

@app.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "terminal_create":
        session_id = arguments.get("session_name") or f"t_{uuid.uuid4().hex[:8]}"
        cols = arguments.get("cols", 220)
        rows = arguments.get("rows", 50)
        cmd = arguments["command"]
        host = arguments.get("host") or None

        if host:
            tmux_remote(host, ["new-session", "-d", "-s", session_id, "-x", str(cols), "-y", str(rows), cmd])
        else:
            tmux(["new-session", "-d", "-s", session_id, "-x", str(cols), "-y", str(rows), cmd])

        sessions[session_id] = {
            "command": cmd,
            "created_at": datetime.now().isoformat(),
            "host": host,
        }
        return [types.TextContent(type="text", text=json.dumps({"session_id": session_id, "status": "created"}))]

    elif name == "terminal_read":
        session_id = arguments["session_id"]
        lines = arguments.get("lines", 50)
        output = tmux_for(session_id, ["capture-pane", "-t", session_id, "-p", "-S", f"-{lines}"])
        if arguments.get("strip_ansi", True):
            output = strip_ansi(output)
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
        return [types.TextContent(type="text", text='{"status": "sent"}')]

    elif name == "terminal_send_raw":
        session_id = arguments["session_id"]
        tmux_for(session_id, ["send-keys", "-t", session_id, arguments["keys"]])
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
                return [types.TextContent(type="text", text=json.dumps({
                    "found": True,
                    "elapsed": round(elapsed, 1),
                    "output": output,
                }))]
            await asyncio.sleep(poll)
        return [types.TextContent(type="text", text=json.dumps({"found": False, "elapsed": timeout}))]

    elif name == "terminal_close":
        session_id = arguments["session_id"]
        tmux_for(session_id, ["kill-session", "-t", session_id])
        sessions.pop(session_id, None)
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

        return [types.TextContent(type="text", text=json.dumps(result))]

    elif name == "terminal_resize":
        session_id = arguments["session_id"]
        cols = arguments.get("cols", 220)
        rows = arguments.get("rows", 50)
        tmux_for(session_id, ["resize-window", "-t", session_id, "-x", str(cols), "-y", str(rows)])
        return [types.TextContent(type="text", text='{"status": "resized"}')]

    else:
        return [types.TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options(),
        )

if __name__ == "__main__":
    asyncio.run(main())
