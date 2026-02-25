<#
.SYNOPSIS
    Instala y configura OpenSSH Server + MSYS2 + tmux en Windows para
    permitir orquestación de sesiones de terminal desde WSL via SSH.

.DESCRIPTION
    Prepara Windows como host de sesiones tmux accesible desde WSL.
    Esto habilita el patrón OAD (Orchestrated Autonomous Development)
    donde un agente en WSL orquesta procesos Windows interactivos.

    Componentes instalados:
    - OpenSSH Server (servicio Windows nativo)
    - MSYS2 (entorno Unix-like para Windows)
    - tmux (via pacman en MSYS2)
    - Clave pública SSH de WSL en authorized_keys de Windows
    - MSYS2/usr/bin en PATH del sistema

    Idempotente: seguro de ejecutar varias veces. No modifica
    instalaciones existentes de MSYS2 ni otras herramientas.

.PARAMETER WslUser
    Usuario de WSL cuya clave pública se importará. Default: autodetectado.

.PARAMETER Msys2Path
    Ruta de instalación de MSYS2. Default: C:\msys64 o autodetectado.

.PARAMETER PubKey
    Clave pública SSH en formato "ssh-ed25519 AAAA... comentario".
    Si se omite, se autodetecta desde WSL. Usa este parámetro si la
    autodetección falla.

.EXAMPLE
    # Forma recomendada: usar el launcher (usuario normal, lanza UAC automaticamente):
    powershell -ExecutionPolicy Bypass -File setup-windows-user.ps1

.EXAMPLE
    # Directamente como administrador con clave explicita:
    .\setup-windows-ssh-tmux.ps1 -PubKey "ssh-ed25519 AAAAC3N... wsl-terminal-mcp"
#>

param(
    [string]$WslUser = "",
    [string]$Msys2Path = "",
    [string]$PubKey = ""
)

# Fijar encoding UTF-8 para evitar caracteres corruptos en la consola
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

# Verificar que se ejecuta como administrador
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "Este script requiere privilegios de administrador." -ForegroundColor Red
    Write-Host "Usa setup-windows-user.ps1 para lanzarlo automaticamente con UAC." -ForegroundColor Yellow
    exit 1
}

$ErrorActionPreference = 'Stop'
$VerbosePreference = 'Continue'

function Write-Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Write-OK($msg)   { Write-Host "  OK  $msg" -ForegroundColor Green }
function Write-Skip($msg) { Write-Host " SKIP $msg" -ForegroundColor DarkGray }
function Write-Warn($msg) { Write-Host " WARN $msg" -ForegroundColor Yellow }
function Write-Fail($msg) { Write-Host " FAIL $msg" -ForegroundColor Red }

# ─────────────────────────────────────────────
# 0. Autodetección de entorno WSL
# ─────────────────────────────────────────────
Write-Step "Detectando entorno WSL"

if (-not $WslUser) {
    try {
        $WslUser = (wsl -e whoami 2>$null).Trim() -replace '[^\x20-\x7E]', ''
        Write-OK "Usuario WSL detectado: $WslUser"
    } catch {
        $WslUser = $env:USERNAME
        Write-Warn "No se pudo detectar usuario WSL. Usando: $WslUser"
    }
}

# Detectar distro WSL activa
$wslDistro = ""
try {
    $rawDistros = wsl -l -q 2>$null
    # wsl -l -q en Windows devuelve UTF-16; limpiar caracteres nulos
    $wslDistro = ($rawDistros -split "`r?`n" | Where-Object {
        $clean = $_ -replace '[^\x20-\x7E]', ''
        $clean -and $clean -notmatch 'docker|swarm'
    } | Select-Object -First 1) -replace '[^\x20-\x7E]', ''
    Write-OK "Distro WSL: $wslDistro"
} catch {
    Write-Warn "No se pudo detectar distro WSL."
}

# ─────────────────────────────────────────────
# 1. OpenSSH Server
# ─────────────────────────────────────────────
Write-Step "OpenSSH Server"

$sshCapability = Get-WindowsCapability -Online -Name 'OpenSSH.Server*' -ErrorAction SilentlyContinue
if ($sshCapability -and $sshCapability.State -eq 'Installed') {
    Write-Skip "OpenSSH Server ya instalado."
} else {
    Write-Host "  Instalando OpenSSH Server..."
    Add-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0' | Out-Null
    Write-OK "OpenSSH Server instalado."
}

$sshd = Get-Service -Name sshd -ErrorAction SilentlyContinue
if ($sshd) {
    Set-Service -Name sshd -StartupType Automatic
    if ($sshd.Status -ne 'Running') {
        Start-Service sshd
        Write-OK "sshd iniciado y configurado como automático."
    } else {
        Write-Skip "sshd ya en ejecución."
        Set-Service -Name sshd -StartupType Automatic
    }
} else {
    Write-Fail "No se encontró el servicio sshd tras la instalación."
}

# Regla de firewall
$fwRule = Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue
if ($fwRule) {
    Write-Skip "Regla de firewall SSH ya existe."
} else {
    New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' `
        -DisplayName 'OpenSSH Server (sshd)' `
        -Enabled True -Direction Inbound -Protocol TCP `
        -Action Allow -LocalPort 22 | Out-Null
    Write-OK "Regla de firewall SSH creada."
}

# ─────────────────────────────────────────────
# 2. MSYS2
# ─────────────────────────────────────────────
Write-Step "MSYS2"

# Buscar instalación existente
if (-not $Msys2Path) {
    foreach ($candidate in @('C:\msys64', 'C:\msys2', 'C:\tools\msys64')) {
        if (Test-Path "$candidate\usr\bin\bash.exe") {
            $Msys2Path = $candidate
            break
        }
    }
}

if ($Msys2Path) {
    Write-Skip "MSYS2 encontrado en: $Msys2Path"
} else {
    $Msys2Path = 'C:\msys64'
    Write-Host "  Instalando MSYS2 via winget en $Msys2Path ..."

    # Verificar que winget está disponible
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "winget no disponible. Instala MSYS2 manualmente desde https://www.msys2.org/ en C:\msys64"
    }

    winget install MSYS2.MSYS2 --silent --accept-package-agreements `
        --accept-source-agreements --location $Msys2Path 2>&1 | Out-Null

    # Primera inicialización (actualiza la base de datos de pacman)
    Write-Host "  Inicializando MSYS2 (primera ejecución)..."
    & "$Msys2Path\usr\bin\bash.exe" -lc "true" 2>$null
    Start-Sleep -Seconds 3
    & "$Msys2Path\usr\bin\bash.exe" -lc "pacman -Sy --noconfirm 2>&1 | tail -3" 2>$null
    Write-OK "MSYS2 instalado e inicializado."
}

# ─────────────────────────────────────────────
# 3. tmux
# ─────────────────────────────────────────────
Write-Step "tmux"

$tmuxBin = "$Msys2Path\usr\bin\tmux.exe"
if (Test-Path $tmuxBin) {
    $tmuxVer = & $tmuxBin -V 2>$null
    Write-Skip "tmux ya instalado: $tmuxVer"
} else {
    Write-Host "  Instalando tmux via pacman..."
    & "$Msys2Path\usr\bin\bash.exe" -lc "pacman -S --noconfirm tmux 2>&1 | tail -5"
    if (Test-Path $tmuxBin) {
        Write-OK "tmux instalado."
    } else {
        Write-Fail "No se pudo instalar tmux. Ejecuta manualmente en MSYS2: pacman -S tmux"
    }
}

# ─────────────────────────────────────────────
# 4. PATH del sistema — MSYS2 usr/bin
# ─────────────────────────────────────────────
Write-Step "PATH del sistema"

$msys2Bin  = "$Msys2Path\usr\bin"
$systemPath = [Environment]::GetEnvironmentVariable('PATH', 'Machine')

if ($systemPath -like "*$msys2Bin*") {
    Write-Skip "MSYS2 usr/bin ya en PATH del sistema."
} else {
    [Environment]::SetEnvironmentVariable('PATH', "$msys2Bin;$systemPath", 'Machine')
    Write-OK "Añadido $msys2Bin al PATH del sistema."
    Write-Warn "Reinicia la sesión SSH o abre un terminal nuevo para que el PATH tenga efecto."
}

# ─────────────────────────────────────────────
# 5. Clave SSH de WSL → Windows authorized_keys
# ─────────────────────────────────────────────
Write-Step "Clave SSH de WSL"

$resolvedKey = $PubKey.Trim()

if (-not $resolvedKey) {
    # Intentar via ruta UNC (\\wsl.localhost\Distro\home\user\...)
    if ($wslDistro) {
        $keyPaths = @(
            "\\wsl.localhost\$wslDistro\home\$WslUser\.ssh\id_ed25519.pub",
            "\\wsl.localhost\$wslDistro\home\$WslUser\.ssh\id_rsa.pub",
            "\\wsl$\$wslDistro\home\$WslUser\.ssh\id_ed25519.pub",
            "\\wsl$\$wslDistro\home\$WslUser\.ssh\id_rsa.pub"
        )
        foreach ($path in $keyPaths) {
            if (Test-Path $path -ErrorAction SilentlyContinue) {
                $resolvedKey = (Get-Content $path -Raw -Encoding UTF8).Trim()
                Write-OK "Clave pública encontrada: $path"
                break
            }
        }
    }
}

if (-not $resolvedKey) {
    # Intentar via wsl -- (más robusto que wsl -e bash -c)
    try {
        $raw = wsl -- sh -c "cat ~/.ssh/id_ed25519.pub 2>/dev/null || cat ~/.ssh/id_rsa.pub 2>/dev/null" 2>$null
        $resolvedKey = ($raw -join '' ).Trim() -replace '[^\x20-\x7E]', ''
        if ($resolvedKey -notmatch '^ssh-') { $resolvedKey = $null }
        else { Write-OK "Clave obtenida via wsl --" }
    } catch { $resolvedKey = $null }
}

if (-not $resolvedKey) {
    Write-Warn "No se encontró clave pública SSH en WSL."
    Write-Warn "Opciones:"
    Write-Warn "  1. Genera una en WSL: ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N ''"
    Write-Warn "  2. Pasa la clave directamente:"
    Write-Warn "     .\setup-windows-ssh-tmux.ps1 -PubKey `"`$(cat ~/.ssh/id_ed25519.pub)`""
    Write-Warn "  3. Añádela manualmente a: C:\ProgramData\ssh\administrators_authorized_keys"
} else {
    $pubKey = $resolvedKey
    # En Windows, los administradores usan administrators_authorized_keys
    $sshdDataDir = 'C:\ProgramData\ssh'
    if (-not (Test-Path $sshdDataDir)) { New-Item -ItemType Directory -Path $sshdDataDir -Force | Out-Null }

    $authKeysFile = "$sshdDataDir\administrators_authorized_keys"
    if (-not (Test-Path $authKeysFile)) {
        New-Item -ItemType File -Path $authKeysFile -Force | Out-Null
    }

    $existing = Get-Content $authKeysFile -ErrorAction SilentlyContinue
    if ($existing -match [regex]::Escape($pubKey.Split(' ')[1])) {
        Write-Skip "Clave ya presente en authorized_keys."
    } else {
        Add-Content -Path $authKeysFile -Value $pubKey
        Write-OK "Clave añadida a $authKeysFile"
    }

    # Permisos requeridos por OpenSSH en Windows (herencia desactivada)
    icacls $authKeysFile /inheritance:r /grant "Administrators:(F)" /grant "SYSTEM:(F)" 2>$null | Out-Null
    Write-OK "Permisos de authorized_keys configurados."
}

# ─────────────────────────────────────────────
# 6. Verificación final
# ─────────────────────────────────────────────
Write-Step "Verificación final"

$authKeysOk = $false
$authKeysPath = 'C:\ProgramData\ssh\administrators_authorized_keys'
if (Test-Path $authKeysPath) {
    $authContent = Get-Content $authKeysPath -ErrorAction SilentlyContinue
    $authKeysOk = ($authContent | Where-Object { $_ -match '^ssh-' }) -ne $null
}

$results = @{
    "sshd corriendo"      = (Get-Service sshd -ErrorAction SilentlyContinue).Status -eq 'Running'
    "tmux instalado"      = Test-Path $tmuxBin
    "MSYS2 en PATH"       = ([Environment]::GetEnvironmentVariable('PATH','Machine')) -like "*$msys2Bin*"
    "authorized_keys"     = $authKeysOk
}

$allOk = $true
foreach ($check in $results.GetEnumerator()) {
    if ($check.Value) { Write-OK $check.Key } else { Write-Fail $check.Key; $allOk = $false }
}

Write-Host ""
if ($allOk) {
    Write-Host "Setup completo." -ForegroundColor Green
    Write-Host ""
    Write-Host "Prueba desde WSL:" -ForegroundColor White
    Write-Host "  ssh localhost 'tmux new-session -d -s test -x 220 -y 50 cmd /c echo OK && tmux capture-pane -t test -p && tmux kill-session -t test'" -ForegroundColor DarkCyan
    Write-Host ""
    Write-Host "Si la clave SSH no funciona, copia manualmente la salida de:" -ForegroundColor White
    Write-Host "  cat ~/.ssh/id_ed25519.pub    (en WSL)" -ForegroundColor DarkCyan
    Write-Host "a: C:\ProgramData\ssh\administrators_authorized_keys  (en Windows)" -ForegroundColor DarkCyan
} else {
    Write-Host "Algunos componentes necesitan atencion. Revisa los errores anteriores." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Si fallo la clave SSH, ejecuta de nuevo pasando la clave directamente:" -ForegroundColor White
    Write-Host "  En WSL:     cat ~/.ssh/id_ed25519.pub" -ForegroundColor DarkCyan
    Write-Host "  En Windows: .\setup-windows-ssh-tmux.ps1 -PubKey ""<pega la clave aqui>""" -ForegroundColor DarkCyan
}
