# Registers the "SideTap tunneld" scheduled task: pymobiledevice3's tunneld (scripts\tunneld.pyw),
# with no window and admin rights (it creates TUN interfaces), started at logon and restarted
# every minute if it stops. tunneld keeps the tunnel to the phone over USB or Wi-Fi.
# Run from an admin PowerShell; re-running replaces the task.
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_tunneld_task.ps1 -Python <python.exe>
# -Python is the interpreter SideTap's packages are installed in (pythonw.exe next to it runs the task).
# -TaskName matches TUNNELD_TASK in .env (default "SideTap tunneld").
# -LogDir holds tunneld.log. It and the task's working directory stay outside the app folder,
# so the running task never blocks the installer from replacing that folder.

param(
    [Parameter(Mandatory = $true)][string]$Python,
    [string]$TaskName = 'SideTap tunneld',
    [string]$LogDir = (Join-Path $env:LOCALAPPDATA 'SideTap\tunneld'),
    [switch]$NoStart
)

$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $identity.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this from an admin PowerShell: tunneld needs admin rights to create its network interfaces.'
}
$Repo = Split-Path -Parent $PSScriptRoot
$Pythonw = Join-Path (Split-Path -Parent $Python) 'pythonw.exe'
$Script = Join-Path $PSScriptRoot 'tunneld.pyw'
foreach ($f in $Pythonw, $Script) { if (-not (Test-Path $f)) { throw "missing $f" } }

$user = "$env:USERDOMAIN\$env:USERNAME"
# tunneld.pyw creates $LogDir itself, as the task: a folder made here can land in an app
# package's private copy of AppData (MSIX redirection) that the task never sees. The working
# directory is the interpreter's folder, which always exists and is outside the app folder.
$action = New-ScheduledTaskAction -Execute $Pythonw -Argument "`"$Script`" `"$LogDir`"" `
    -WorkingDirectory (Split-Path -Parent $Pythonw)
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew -StartWhenAvailable
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal `
    -Settings $settings -Description "pymobiledevice3 tunneld for SideTap ($Repo)" -Force | Out-Null
Write-Host "Registered '$TaskName' -> $Pythonw $Script (log: $LogDir\tunneld.log)"
if (-not $NoStart) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "Started '$TaskName'"
}
