# Put a "SideTap" shortcut on the Desktop and in the Start Menu.
# Double-click result: minimized console, viewer opens in the browser.
# -Startup also makes SideTap start when Windows starts.
param([switch]$Startup)
$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent $PSScriptRoot
$target = Join-Path $repo 'sidetap.cmd'
$icon = Join-Path $repo 'assets\sidetap.ico'
if (-not (Test-Path $target)) { throw "not found: $target" }

$shell = New-Object -ComObject WScript.Shell
$places = @(
    [Environment]::GetFolderPath('Desktop'),
    (Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs')
)
if ($Startup) { $places += [Environment]::GetFolderPath('Startup') }
foreach ($dir in $places) {
    # Rewriting a file keeps its existing casing on Windows, so a shortcut left
    # over from the old "Sidetap" spelling would survive the rename in name
    # only. Drop it first so the Desktop actually reads SideTap.
    $stale = Get-ChildItem -LiteralPath $dir -Filter 'SideTap.lnk' -Force -ErrorAction SilentlyContinue
    if ($stale -and $stale.Name -cne 'SideTap.lnk') { Remove-Item -LiteralPath $stale.FullName -Force }
    $lnk = $shell.CreateShortcut((Join-Path $dir 'SideTap.lnk'))
    $lnk.TargetPath = $target
    $lnk.WorkingDirectory = $repo
    $lnk.IconLocation = $icon
    $lnk.WindowStyle = 7   # start minimized; the browser tab is the real surface
    $lnk.Description = 'Start SideTap: phone viewer + USB link'
    $lnk.Save()
    Write-Output "created: $(Join-Path $dir 'SideTap.lnk')"
}
