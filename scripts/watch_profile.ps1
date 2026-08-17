# Capture the provisioning profile Sideloadly mints while it signs WDA.
#
# TWO nets, because one was measured missing it. A FileSystemWatcher is fast
# but its kernel buffer is 64KB and SILENTLY DROPS events while the watched
# tree is busy - and on 2026-08-16 a Sideloadly 0.60 sign that succeeded
# (installations.db moved, no error) left no .mobileprovision under any watched
# root at all. So a 250ms poll of anything created since we armed runs beside
# the events, and every root, every sighting and every dropped-event overflow
# is PRINTED - a capture that fails must say what it saw, not just time out.
#
# Lines the caller parses (one per line, flushed as they happen):
#   watching <root>              armed on this root
#   READY                        all roots armed
#   WAITING <seconds-left>       heartbeat, so the UI can count down
#   SAW <path>                   a .mobileprovision was seen
#   OVERFLOW <root>              the kernel dropped events on this root
#   SIDELOADLY_RAN               Sideloadly's install DB moved: the sign happened
#   CAPTURED_FROM <path>         bytes copied to -OutFile
#   PROFILE_SAVED <out>          exit 0
#   NO_PROFILE                   nothing signed within the window, exit 1
#   NO_PROFILE_AFTER_SIDELOADLY  Sideloadly signed but wrote no profile, exit 2
param(
  [string]$OutFile = 'C:\Projects\phone-claude\.state\captured.mobileprovision',
  [int]$TimeoutSec = 300,
  [string]$InstallDb = "$env:LOCALAPPDATA\Sideloadly\installations.db"
)
$ErrorActionPreference = 'SilentlyContinue'
$global:Out = $OutFile
if (Test-Path $global:Out) { Remove-Item -LiteralPath $global:Out -Force }

# One grab used by BOTH nets, so a sighting behaves the same however it arrived.
# The size floor rejects a half-written file; the retry loop waits it out.
$global:Grab = {
  param($p)
  if ($p -notmatch '\.mobileprovision$' -or $p -match 'pytest') { return }
  if (Test-Path $global:Out) { return }
  Write-Host "SAW $p"
  for ($i = 0; $i -lt 20 -and -not (Test-Path $global:Out); $i++) {
    try {
      $bytes = [System.IO.File]::ReadAllBytes($p)
      if ($bytes.Length -gt 1000) {
        [System.IO.File]::WriteAllBytes($global:Out, $bytes)
        Write-Host "CAPTURED_FROM $p"
        return
      }
    } catch { }
    Start-Sleep -Milliseconds 30
  }
}

# Big, busy trees: events plus a bounded scan of subdirectories born after we
# armed (Sideloadly's layout would be tmpXXXX\Payload\Foo.app\embedded...).
$big = @("$env:TEMP", "$env:LOCALAPPDATA\Temp", "$env:WINDIR\Temp",
         "$env:WINDIR\System32\config\systemprofile\AppData\Local\Temp")
# Small, quiet trees: cheap enough to walk whole, every tick.
$small = @("$env:LOCALAPPDATA\cache\sideloadly", "$env:APPDATA\sideloadly")
# Watched for events only - thousands of static files, not worth polling.
$watchOnly = @("$env:LOCALAPPDATA\Sideloadly")

$big = @($big | Select-Object -Unique | Where-Object { Test-Path $_ })
$small = @($small | Where-Object { Test-Path $_ })
$roots = @($big + $small + $watchOnly | Select-Object -Unique | Where-Object { Test-Path $_ })

$action = { & $global:Grab $Event.SourceEventArgs.FullPath }
$onError = { Write-Host "OVERFLOW $($Event.MessageData)" }
$watchers = @()
foreach ($r in $roots) {
  $w = New-Object System.IO.FileSystemWatcher $r, '*.mobileprovision'
  $w.IncludeSubdirectories = $true
  $w.InternalBufferSize = 65536
  $w.EnableRaisingEvents = $true
  Register-ObjectEvent -InputObject $w -EventName Created -Action $action | Out-Null
  Register-ObjectEvent -InputObject $w -EventName Changed -Action $action | Out-Null
  Register-ObjectEvent -InputObject $w -EventName Error -MessageData $r -Action $onError | Out-Null
  $watchers += $w
  Write-Host "watching $r"
}
Write-Host "READY"

$armed = Get-Date
$deadline = $armed.AddSeconds($TimeoutSec)
# Sideloadly rewrites this on every install. It is the only durable proof the
# human's Start click actually ran, so a capture that misses can still say
# "Sideloadly signed, the profile just was not on disk" instead of "timed out".
$db = $InstallDb
$dbStamp = if (Test-Path $db) { (Get-Item $db).LastWriteTimeUtc } else { $null }
$sideloadlyRan = $false
$nextBeat = $armed

while ((Get-Date) -lt $deadline -and -not (Test-Path $global:Out)) {
  Start-Sleep -Milliseconds 250
  foreach ($r in $roots) {
    Get-ChildItem -LiteralPath $r -Filter '*.mobileprovision' -File -ErrorAction SilentlyContinue |
      Where-Object { $_.LastWriteTime -ge $armed } |
      ForEach-Object { & $global:Grab $_.FullName }
  }
  foreach ($r in $big) {
    Get-ChildItem -LiteralPath $r -Directory -ErrorAction SilentlyContinue |
      Where-Object { $_.CreationTime -ge $armed } |
      ForEach-Object {
        Get-ChildItem -LiteralPath $_.FullName -Filter '*.mobileprovision' -File `
                      -Recurse -Depth 4 -ErrorAction SilentlyContinue |
          ForEach-Object { & $global:Grab $_.FullName }
      }
  }
  foreach ($r in $small) {
    Get-ChildItem -LiteralPath $r -Filter '*.mobileprovision' -File `
                  -Recurse -Depth 4 -ErrorAction SilentlyContinue |
      ForEach-Object { & $global:Grab $_.FullName }
  }
  if (Test-Path $global:Out) { break }

  if (-not $sideloadlyRan -and $dbStamp -and (Test-Path $db) -and
      (Get-Item $db).LastWriteTimeUtc -gt $dbStamp) {
    $sideloadlyRan = $true
    Write-Host "SIDELOADLY_RAN"
    # The sign is over. Give the profile a short grace to appear, then stop -
    # sitting out the rest of a 10 minute window teaches the human nothing.
    $late = (Get-Date).AddSeconds(60)
    if ($late -lt $deadline) { $deadline = $late }
  }
  if ((Get-Date) -ge $nextBeat) {
    $nextBeat = (Get-Date).AddSeconds(5)
    Write-Host ("WAITING " + [Math]::Max(0, [int]($deadline - (Get-Date)).TotalSeconds))
  }
}

foreach ($w in $watchers) { $w.EnableRaisingEvents = $false; $w.Dispose() }
Get-EventSubscriber | Unregister-Event
if (Test-Path $global:Out) { Write-Host "PROFILE_SAVED $global:Out"; exit 0 }
if ($sideloadlyRan) { Write-Host "NO_PROFILE_AFTER_SIDELOADLY"; exit 2 }
Write-Host "NO_PROFILE"
exit 1
