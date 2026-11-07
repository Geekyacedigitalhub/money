# Supervisor for the bot on a Windows server.
#
# Started by the scheduled task that install-autostart.ps1 registers. It runs the
# bot in a loop so a crash restarts it, and appends everything to one log file.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File deploy\windows\run-bot.ps1
#
# Adjust $RootDir below if the checkout lives somewhere other than C:\money.

param(
    [string]$RootDir = "C:\money",
    [int]$Port = 8080
)

$ErrorActionPreference = "Stop"

$python = Join-Path $RootDir ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Error "no virtualenv at $python - create it first: py -3.12 -m venv .venv"
    exit 1
}

$logDir = Join-Path $RootDir "storage\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir "bot.log"

function Write-Log([string]$Message) {
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -Path $log -Value $line -Encoding utf8
}

Write-Log "supervisor starting (root=$RootDir port=$Port)"

# Anything already listening on the port is a stale run from before a reboot.
$stale = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
foreach ($conn in $stale) {
    try {
        Stop-Process -Id $conn.OwningProcess -Force -ErrorAction Stop
        Write-Log "stopped stale process $($conn.OwningProcess) on port $Port"
    } catch {
        Write-Log "could not stop pid $($conn.OwningProcess): $_"
    }
}

Set-Location $RootDir
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

while ($true) {
    Write-Log "starting bot"
    try {
        # --autopilot is what makes it render and post on a schedule without you.
        & $python -m app.bot.cli serve --autopilot --port $Port *>> $log
        Write-Log "bot exited with code $LASTEXITCODE"
    } catch {
        Write-Log "bot crashed: $_"
    }
    Start-Sleep -Seconds 15
}
