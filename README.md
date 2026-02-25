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
- For Windows sessions: [setup guide](docs/windows-setup.md)

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

## License

MIT
