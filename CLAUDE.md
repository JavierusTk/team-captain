# Team Captain — Claude Code Guide

MCP server for orchestrating multi-agent terminal sessions via tmux.

## Files

- `server.py` — single-file MCP server (~200 lines)
- `requirements.txt` — `mcp>=1.0.0`, `pydantic>=2.0`

## Key design decisions

**Sessions dict** stores `{command, created_at, host}` per session. `host=None` means local WSL; `host="localhost"` (or any SSH target) means Windows via SSH.

**`tmux_for(session_id, args)`** dispatches to `tmux()` (local) or `tmux_remote()` (SSH) based on session metadata. All read/send/wait/close/resize operations go through this.

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
