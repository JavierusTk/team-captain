# Team Captain — Claude Code Guide

MCP server for orchestrating multi-agent terminal sessions via tmux.

## Files

- `server.py` — single-file MCP server (~250 lines)
- `requirements.txt` — `mcp>=1.0.0`, `pydantic>=2.0`

## Key design decisions

**Sessions dict** stores `{command, created_at, host}` per session. `host=None` means local WSL; `host="localhost"` (or any SSH target) means Windows via SSH.

**`tmux_for(session_id, args)`** dispatches to `tmux()` (local) or `tmux_remote()` (SSH) based on session metadata. All read/send/wait/close/resize operations go through this.

**Error handling**: `call_tool()` delegates to `_dispatch()` wrapped in try/except. `subprocess.TimeoutExpired` → `TIMEOUT`, generic exceptions → `INTERNAL_ERROR`. SSH health is checked before remote session creation → `SSH_UNREACHABLE`. Helper: `_error(message, code)` returns structured JSON.

**Logging**: All logging goes to stderr via `logging.basicConfig(stream=sys.stderr)`. Every tool call logs session_id, timing, and duration. stdout is reserved for MCP protocol. `LOG_LEVEL` env var controls verbosity (default: `INFO`).

**Subprocess timeouts**: All `subprocess.run()` calls use `timeout=SUBPROCESS_TIMEOUT` (30s) to prevent hung SSH connections from blocking the server indefinitely.

**`terminal_cleanup`** tool removes dead sessions from the `sessions` dict. Explicit operation — `terminal_list` never mutates state as a side effect.

**Windows tmux**: MSYS2 tmux needs its own environment to resolve `/tmp` socket path. All remote tmux commands run via:
```
ssh HOST 'C:/msys64/usr/bin/bash.exe -lc "tmux ..."'
```
Quoting: `shlex.quote()` for bash args, then `replace('"', '\\"')` to escape double quotes for cmd.exe.

## Running locally

```bash
.venv/bin/python3 server.py
```

## MCP registration (in `~/.claude/mcp.json`)

```json
"terminal": {
  "command": "/path/to/.venv/bin/python3",
  "args": ["/path/to/server.py"],
  "env": {}
}
```

The MCP entry name (`"terminal"`) is independent from the project name.

## Windows setup

See `docs/windows-setup.md`. Requires OpenSSH Server + MSYS2 + tmux on Windows, and SSH key from WSL in `C:\ProgramData\ssh\administrators_authorized_keys`.
