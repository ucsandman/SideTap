# Blocks LAN access to WebDriverAgent (:8100) and its MJPEG stream (:9100).
#
# go-ios has no bind-address flag, so `ios forward` listens on 0.0.0.0 and WDA
# has no authentication. This adds one inbound-block Windows Firewall rule.
# Windows Firewall does NOT filter loopback, so the local viewer and Python
# client keep working; only other machines on the network lose access.
#
# Self-elevates (UAC prompt) if not already admin. Idempotent: a second run
# with the rule already present does nothing. Writes .state\lock_ports.log so a
# failure in the elevated window is not lost when it closes.
#   powershell -ExecutionPolicy Bypass -File scripts\lock_ports.ps1

$ErrorActionPreference = 'Stop'
$RuleName = 'phone-harness block LAN'
$LogDir = Join-Path $PSScriptRoot '..\.state'
$Log = Join-Path $LogDir 'lock_ports.log'

function Write-Log($msg) {
    try {
        if (-not (Test-Path $LogDir)) {
            New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
        }
        "$(Get-Date -Format o)  $msg" | Add-Content -Path $Log -Encoding UTF8
    } catch { }
}

$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent() `
).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Write-Log "not admin; relaunching elevated from $PSCommandPath"
    # Single-string ArgumentList — the array form re-quotes an already-quoted
    # path and hands the elevated shell a broken -File value.
    Start-Process -FilePath 'powershell.exe' -Verb RunAs `
        -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    exit 0
}

try {
    if (Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue) {
        Write-Log "rule already present"
        Write-Host "Already locked: firewall rule '$RuleName' exists."
        exit 0
    }
    New-NetFirewallRule `
        -DisplayName $RuleName `
        -Direction Inbound `
        -Action Block `
        -Protocol TCP `
        -LocalPort 8100, 9100 `
        -Profile Any | Out-Null
    Write-Log "rule created"
    Write-Host "Locked: blocked inbound TCP 8100/9100 from the network."
} catch {
    Write-Log "ERROR: $_"
    Write-Host "FAILED: $_"
    Start-Sleep -Seconds 10  # keep the elevated window open so the error is readable
    exit 1
}
