# Team Captain

An MCP server that lets Claude orchestrate interactive terminal sessions — locally in WSL or remotely on Windows via SSH.

Enables the **OAD pattern** (Orchestrated Autonomous Development): one Claude instance acts as captain, spawning and controlling subordinate agents, CLI tools, and interactive programs (other Claude instances, Flutter, Delphi compilers, BMad...) without human relay.

## How it works

Team Captain exposes tmux sessions as MCP tools. The orchestrating Claude can:

- Create sessions running any command
- Read terminal output at any point
- Send keystrokes (text, special keys, raw sequences)
- Wait for specific output patterns
- Close sessions when done

Sessions can run locally (WSL) or on a remote Windows host via SSH + MSYS2.

## Installation

### Prerequisites

- tmux (`sudo apt install tmux`)
- Python 3.10+
- For Windows sessions: run `windows-setup\setup-windows-user.ps1` — [full guide](docs/windows-setup.md)

### Install

```bash
git clone https://github.com/JavierusTk/team-captain
cd team-captain
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### Register with Claude Code

```bash
claude mcp add --scope user terminal -- \
  /path/to/team-captain/.venv/bin/python3 \
  /path/to/team-captain/server.py
```

## Usage

### Local session (WSL)

```python
terminal_create(command="claude-nested /path/to/project", session_name="worker")
terminal_wait(session_id="worker", pattern="Esc to cancel", timeout_seconds=30)
terminal_send_raw(session_id="worker", keys="Escape")
terminal_send(session_id="worker", text="implement the login feature")
terminal_read(session_id="worker", lines=50)
terminal_close(session_id="worker")
```

### Windows session (via SSH)

```python
terminal_create(command="powershell.exe", session_name="win", host="localhost")
terminal_send(session_id="win", text="flutter run -d windows")
terminal_wait(session_id="win", pattern="Running on", timeout_seconds=60)
```

## Tools

| Tool | Description |
|------|-------------|
| `terminal_create` | Create a tmux session running a command |
| `terminal_read` | Capture current terminal output |
| `terminal_send` | Send text (literal, safe) |
| `terminal_send_raw` | Send raw key sequences (Up, C-c, Escape...) |
| `terminal_wait` | Wait until output matches a pattern |
| `terminal_close` | Kill a session |
| `terminal_list` | List active sessions |
| `terminal_resize` | Resize a session window |
| `terminal_cleanup` | Remove dead sessions from tracking |

## Error handling

All tool calls return structured JSON errors instead of crashing:

```json
{"error": "Cannot reach host 'myhost' via SSH: connection timed out", "code": "SSH_UNREACHABLE"}
```

Error codes: `TIMEOUT`, `SSH_UNREACHABLE`, `INTERNAL_ERROR`, `UNKNOWN_TOOL`.

SSH connectivity is verified before creating remote sessions (fail-fast). All subprocess calls have a 30-second timeout to prevent hung connections from blocking the server.

## Logging

Logs go to **stderr** (stdout is reserved for the MCP protocol). Every tool call is logged with session ID and timing. Set `LOG_LEVEL` environment variable to control verbosity.

## Practical limits

There is no hard-coded session cap. Practical limits come from the environment:

- **tmux** handles hundreds of sessions without issue; it's not the bottleneck.
- **SSH connections** are the real constraint for remote sessions — each `terminal_read`/`terminal_send` spawns a subprocess with an SSH call. With many concurrent remote sessions, SSH connection overhead adds up.
- **Memory** is negligible — the server only stores a small dict per session.
- **Rule of thumb**: 5–10 concurrent sessions work well in practice. Beyond that, latency on remote operations increases and you may want to batch work or close idle sessions.

## Architecture: what this is and what it isn't

Team Captain is **infrastructure tooling** — it exposes tmux sessions as MCP tools. It is deliberately minimal (~250 lines, single file).

**What belongs here**: session lifecycle, terminal I/O, error handling, SSH connectivity, logging.

**What does NOT belong here** (and why):
- **Context management** — the calling agent controls its own context window, not the tool server
- **Permission models** — tmux can't enforce command restrictions; real isolation requires OS-level mechanisms (containers, namespaces)
- **Retry policies** — the agent has more context about whether retrying makes sense; the server fails fast
- **Output validation** — the caller knows what format it expects, not the terminal reader
- **Delegation chain tracking** — the server doesn't know about agent hierarchies; that's orchestration-layer concern

This separation keeps the tool reliable and simple. Orchestration complexity belongs in the agent that uses these tools, not in the tools themselves.

## License

MIT
