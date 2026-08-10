# Put a "Sidetap" shortcut on the Desktop and in the Start Menu.
# Double-click result: minimized console, viewer opens in the browser.
# -Startup also makes sidetap start when Windows starts.
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
    $lnk = $shell.CreateShortcut((Join-Path $dir 'Sidetap.lnk'))
    $lnk.TargetPath = $target
    $lnk.WorkingDirectory = $repo
    $lnk.IconLocation = $icon
    $lnk.WindowStyle = 7   # start minimized; the browser tab is the real surface
    $lnk.Description = 'Start sidetap: phone viewer + USB link'
    $lnk.Save()
    Write-Output "created: $(Join-Path $dir 'Sidetap.lnk')"
}
