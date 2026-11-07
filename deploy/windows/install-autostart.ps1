# Make the bot start by itself when the Windows server boots, and keep running
# after you close the RDP window.
#
#   powershell -ExecutionPolicy Bypass -File deploy\windows\install-autostart.ps1
#
# Run it from an Administrator PowerShell. Uses the built-in Task Scheduler, so
# nothing extra to download or licence.
#
# Why a scheduled task and not a shortcut: an RDP session ends when you close the
# window, and anything started from it dies with it. A "run whether the user is
# logged on or not" task survives that, which is the entire point of paying for a
# server that stays on.

param(
    [string]$RootDir = "C:\money",
    [int]$Port = 8080,
    [string]$TaskName = "PostBot",
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "run this from an Administrator PowerShell (right-click -> Run as administrator)"
    exit 1
}

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "removed the '$TaskName' task" -ForegroundColor Green
    } else {
        Write-Host "no '$TaskName' task to remove"
    }
    exit 0
}

$runner = Join-Path $RootDir "deploy\windows\run-bot.ps1"
if (-not (Test-Path $runner)) {
    Write-Error "cannot find $runner - run this from the repository root, or pass -RootDir"
    exit 1
}

$python = Join-Path $RootDir ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Error "no virtualenv at $python - set the server up first (see WINDOWS.md)"
    exit 1
}

Write-Host "installing the '$TaskName' task" -ForegroundColor Cyan

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runner`" -RootDir `"$RootDir`" -Port $Port" `
    -WorkingDirectory $RootDir

$trigger = New-ScheduledTaskTrigger -AtStartup

# Restart if the supervisor itself ever dies, and do not stop the task just
# because Windows thinks the machine is idle or on battery.
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1)

# SYSTEM: no password to store, starts before anyone logs in, survives RDP
# disconnects. The bot only needs file access to its own folder.
$taskPrincipal = New-ScheduledTaskPrincipal `
    -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $taskPrincipal `
    -Description "MoneyPrinterTurbo post bot + autopilot (renders and publishes on schedule)" `
    -Force | Out-Null

# SYSTEM needs write access to the folders the bot uses.
foreach ($relative in @("storage", "config.toml")) {
    $target = Join-Path $RootDir $relative
    if (Test-Path $target) {
        try {
            icacls $target /grant "SYSTEM:(OI)(CI)F" /T /Q | Out-Null
        } catch {
            Write-Host "  ! could not grant SYSTEM access to $target" -ForegroundColor Yellow
        }
    }
}

Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 4

$state = (Get-ScheduledTask -TaskName $TaskName).State
Write-Host ""
Write-Host "  task state : $state" -ForegroundColor Green
Write-Host "  log file   : $RootDir\storage\logs\bot.log"
Write-Host "  check it   : Invoke-RestMethod http://localhost:$Port/api/bot/health"
Write-Host ""
Write-Host "The bot now starts with the server and ignores RDP disconnects." -ForegroundColor Green
Write-Host "Remove it later with: .\deploy\windows\install-autostart.ps1 -Uninstall"
