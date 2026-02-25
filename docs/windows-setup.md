# Windows Setup

Enables Team Captain to create sessions on a Windows host via SSH.

## Requirements

- Windows 10/11
- WSL2 with an SSH key at `~/.ssh/id_ed25519`

## Steps

### 1. Run the user script (no admin required)

From a normal PowerShell window:

```powershell
powershell -ExecutionPolicy Bypass -File setup-windows-user.ps1
```

> **Note**: The scripts are not included in this repo. They are part of the
> development environment that configures the Windows host. The setup installs:
> - OpenSSH Server (Windows native)
> - MSYS2 + tmux
> - Your WSL SSH key in `C:\ProgramData\ssh\administrators_authorized_keys`

A UAC prompt will appear to complete the admin steps.

### 2. Verify from WSL

```bash
ssh localhost 'echo SSH_OK'
ssh localhost 'C:/msys64/usr/bin/bash.exe -lc "tmux -V"'
```

Both should succeed.

### 3. Create a Windows session

```python
terminal_create(command="cmd.exe", session_name="win", host="localhost")
terminal_wait(session_id="win", pattern=">")
terminal_send(session_id="win", text="echo HELLO")
terminal_read(session_id="win")
terminal_close(session_id="win")
```

## How it works

Team Captain runs all tmux commands on Windows through MSYS2 bash:

```
ssh HOST 'C:/msys64/usr/bin/bash.exe -lc "tmux new-session -d -s NAME CMD"'
```

MSYS2's bash provides the Unix environment that tmux needs to resolve its socket path (`/tmp/tmux-*/`). Without it, tmux fails with `no server running on /tmp/...`.
