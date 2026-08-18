<#
collect_wedge_logs.ps1

Usage (run from repo root):
  ./scripts/collect_wedge_logs.ps1 -OutDir .\logs -ReproScript .\reproduce-tiktok-wedge.ps1

What it does:
- Starts `ios syslog` and writes to a timestamped file in OutDir
- Runs the provided repro script (synchronous)
- Stops the syslog capture and prints the saved file path

Notes:
- ios syslog produces ~27 KB/s. A 2 minute capture is a few MB.
- Inspect the saved file before sharing; device names and bundle ids appear in the log.
- This script requires `ios` (go-ios) on PATH.
#>
param(
    [string]$OutDir = "./logs",
    [string]$ReproScript = "./reproduce-tiktok-wedge.ps1",
    [int]$SyslogTimeoutSeconds = 240
)

if (-not (Get-Command ios -ErrorAction SilentlyContinue)) {
    Write-Error "`n`n" + "ios not found on PATH. Install go-ios and ensure `ios` is available before running this script."; exit 2
}

New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
$ts = (Get-Date).ToString('yyyyMMdd-HHmmss')
$logPath = Join-Path (Resolve-Path $OutDir) ("wedge-syslog-$ts.log")

Write-Host "Starting ios syslog -> $logPath"
# Start syslog and redirect stdout to file. -NoNewWindow so output goes to file.
$proc = Start-Process -FilePath ios -ArgumentList 'syslog' -RedirectStandardOutput $logPath -NoNewWindow -PassThru
Start-Sleep -Seconds 1

if (-not $proc -or $proc.HasExited) {
    Write-Error "Failed to start ios syslog."
    exit 3
}

try {
    if (-not (Test-Path $ReproScript)) {
        Write-Warning "Repro script $ReproScript not found. Run it manually while this syslog runs."
        Write-Host "Press Ctrl+C to stop syslog when done."
        Wait-Process -Id $proc.Id
    } else {
        Write-Host "Running repro script: $ReproScript"
        & powershell -ExecutionPolicy Bypass -File $ReproScript
        Write-Host "Repro script finished. Waiting up to $SyslogTimeoutSeconds s for post-recovery logs..."
        Start-Sleep -Seconds ([math]::Min(30, $SyslogTimeoutSeconds))
        # Give a small grace window after recovery to capture tail lines
    }
} finally {
    if (-not $proc.HasExited) {
        Write-Host "Stopping ios syslog (pid $($proc.Id))"
        Stop-Process -Id $proc.Id -ErrorAction SilentlyContinue
    }
}

Write-Host "Saved syslog to: $logPath"
Write-Host "Tip: review $logPath and redact device identifiers or serials before sharing publicly."