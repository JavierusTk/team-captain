<#
.SYNOPSIS
    Paso 1 (usuario normal): lee la clave SSH de WSL y lanza el setup de
    Windows como administrador pasando la clave automaticamente.

.DESCRIPTION
    Ejecutar sin privilegios de administrador. Este script:
      1. Lee la clave publica SSH de WSL (funciona porque no esta elevado)
      2. Lanza setup-windows-ssh-tmux.ps1 como administrador via UAC,
         pasando la clave como parametro -PubKey

.EXAMPLE
    # Ejecutar como usuario normal (doble click o desde terminal):
    powershell -ExecutionPolicy Bypass -File setup-windows-user.ps1
#>

[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

function Write-Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Write-OK($msg)   { Write-Host "  OK  $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host " WARN $msg" -ForegroundColor Yellow }
function Write-Fail($msg) { Write-Host " FAIL $msg" -ForegroundColor Red }

Write-Step "Leyendo clave SSH de WSL"

$pubKey = $null

# ── Estrategia 1: wsl -- whoami + ruta absoluta (evita expansion de ~ por PowerShell) ──
$wslUser = $null
try {
    $raw = wsl -- whoami 2>$null
    $wslUser = ($raw | Where-Object { $_ -match '\w' } | Select-Object -First 1)
    if ($wslUser) { $wslUser = $wslUser.Trim() -replace '[^\x20-\x7E]', '' }
} catch {}

if ($wslUser) {
    foreach ($keyFile in @('id_ed25519.pub', 'id_rsa.pub')) {
        try {
            $raw = wsl -- cat "/home/$wslUser/.ssh/$keyFile" 2>$null
            $found = $raw | Where-Object { $_ -match '^ssh-' } | Select-Object -First 1
            if ($found) { $pubKey = $found.Trim(); break }
        } catch {}
    }
}

# ── Estrategia 2: ruta UNC \\wsl.localhost\Distro\home\user\.ssh\ ──
if (-not $pubKey) {
    $distros = @()
    try {
        # wsl -l -q devuelve UTF-16; limpiar nulos
        $distros = (wsl -l -q 2>$null) |
            ForEach-Object { ($_ -replace '\x00', '').Trim() } |
            Where-Object { $_ -match '^\w' -and $_ -notmatch 'docker|swarm' }
    } catch {}

    if (-not $distros) { $distros = @('Ubuntu', 'Ubuntu-22.04', 'Ubuntu-20.04', 'Debian') }

    $userCandidates = @()
    if ($wslUser) { $userCandidates += $wslUser }
    $userCandidates += $env:USERNAME, 'javier', 'user', 'ubuntu'
    $userCandidates = $userCandidates | Select-Object -Unique

    :outer foreach ($distro in $distros) {
        foreach ($user in $userCandidates) {
            foreach ($keyFile in @('id_ed25519.pub', 'id_rsa.pub')) {
                $path = "\\wsl.localhost\$distro\home\$user\.ssh\$keyFile"
                if (Test-Path $path -ErrorAction SilentlyContinue) {
                    $content = (Get-Content $path -Raw -Encoding UTF8).Trim()
                    if ($content -match '^ssh-') {
                        $pubKey = $content
                        Write-OK "Clave encontrada: $path"
                        break outer
                    }
                }
            }
        }
    }
}

# ── Estrategia 3: pedir la clave al usuario ──
if (-not $pubKey) {
    Write-Warn "No se pudo leer la clave SSH de WSL automaticamente."
    Write-Host ""
    Write-Host "Ejecuta esto en WSL y pega el resultado aqui:" -ForegroundColor Yellow
    Write-Host "  cat ~/.ssh/id_ed25519.pub" -ForegroundColor DarkCyan
    Write-Host ""
    $pubKey = (Read-Host "Pega la clave publica SSH").Trim()
    if (-not $pubKey -or $pubKey -notmatch '^ssh-') {
        Write-Fail "Clave invalida. Debe empezar por 'ssh-ed25519' o 'ssh-rsa'."
        Read-Host "Pulsa Enter para salir"
        exit 1
    }
}

Write-OK "Clave: $($pubKey.Substring(0, [Math]::Min(60, $pubKey.Length)))..."

# ── Localizar script admin ──
$adminScript = Join-Path $PSScriptRoot "setup-windows-ssh-tmux.ps1"
if (-not (Test-Path $adminScript)) {
    Write-Fail "No se encontro setup-windows-ssh-tmux.ps1 en: $PSScriptRoot"
    Read-Host "Pulsa Enter para salir"
    exit 1
}

Write-Step "Lanzando setup admin via UAC"
Write-Host "  Se abrira una ventana de UAC para continuar como administrador." -ForegroundColor White
Write-Host ""

$psArgs = "-ExecutionPolicy Bypass -File `"$adminScript`" -PubKey `"$pubKey`""
Start-Process powershell -Verb RunAs -ArgumentList $psArgs -Wait

Write-Host ""
Write-Host "Setup completado. Verifica la conexion desde WSL:" -ForegroundColor Green
Write-Host "  bash /mnt/w/Repos/Agentic-Coding/Tools/setup-wsl-ssh-key.sh --test" -ForegroundColor DarkCyan
Write-Host ""
Read-Host "Pulsa Enter para salir"
