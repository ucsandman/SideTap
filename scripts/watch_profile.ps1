# Event-driven capture of the provisioning profile Sideloadly mints.
#
# Sideloadly writes embedded.mobileprovision into a %TEMP%\tmpXXXX\...app folder
# and deletes it a few hundred ms later, so a polling scan loses the race. A
# FileSystemWatcher fires on the create event and we copy the bytes immediately.
#
# Prints READY when armed, CAPTURED_FROM <path> on hit, PROFILE_SAVED <out> on
# success (exit 0) or NO_PROFILE on timeout (exit 1).
param(
  [string]$OutFile = 'C:\Projects\phone-claude\.state\captured.mobileprovision',
  [int]$TimeoutSec = 300
)
$ErrorActionPreference = 'SilentlyContinue'
$global:Out = $OutFile
if (Test-Path $global:Out) { Remove-Item -LiteralPath $global:Out -Force }

$roots = @("$env:LOCALAPPDATA\Temp", "$env:WINDIR\Temp", "$env:TEMP")
$roots = $roots | Select-Object -Unique
$watchers = @()
$action = {
  $p = $Event.SourceEventArgs.FullPath
  if ($p -match '\.mobileprovision$' -and $p -notmatch 'pytest' -and -not (Test-Path $global:Out)) {
    for ($i = 0; $i -lt 20 -and -not (Test-Path $global:Out); $i++) {
      try {
        $bytes = [System.IO.File]::ReadAllBytes($p)
        [System.IO.File]::WriteAllBytes($global:Out, $bytes)
        Write-Host "CAPTURED_FROM $p"
        break
      } catch { Start-Sleep -Milliseconds 30 }
    }
  }
}
foreach ($r in $roots) {
  if (Test-Path $r) {
    $w = New-Object System.IO.FileSystemWatcher $r, '*.mobileprovision'
    $w.IncludeSubdirectories = $true
    $w.InternalBufferSize = 65536
    $w.EnableRaisingEvents = $true
    Register-ObjectEvent -InputObject $w -EventName Created -Action $action | Out-Null
    Register-ObjectEvent -InputObject $w -EventName Changed -Action $action | Out-Null
    $watchers += $w
    Write-Host "watching $r"
  }
}
Write-Host "READY"
$deadline = (Get-Date).AddSeconds($TimeoutSec)
while ((Get-Date) -lt $deadline -and -not (Test-Path $global:Out)) { Start-Sleep -Milliseconds 150 }
foreach ($w in $watchers) { $w.EnableRaisingEvents = $false; $w.Dispose() }
Get-EventSubscriber | Unregister-Event
if (Test-Path $global:Out) { Write-Host "PROFILE_SAVED $global:Out"; exit 0 } else { Write-Host "NO_PROFILE"; exit 1 }
