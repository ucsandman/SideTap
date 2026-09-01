# SideTap one-line installer for Windows 10/11.
#
#   irm https://sidetap.io/install.ps1 | iex
#
# What it does (all local, all visible below):
#   1. Installs Python 3.12 if the machine has no Python (via winget).
#   2. Downloads the SideTap app to %LOCALAPPDATA%\SideTap\app.
#   3. Installs the two Python packages SideTap needs (requests, mcp).
#   4. Downloads go-ios (the USB phone tool) — no Node.js needed.
#   5. Installs the Apple Devices app (USB driver) if it is missing.
#   6. Puts a SideTap shortcut on the Desktop and Start Menu.
#
# What it CANNOT do (Apple requires a human for these; SideTap's viewer
# walks you through them after launch):
#   - Enable Developer Mode on the iPhone.
#   - Sign and install WebDriverAgent with your Apple ID (Sideloadly).
#
# Re-running is safe: it updates the app and keeps your .env and .state.
# Overrides for testing: $env:SIDETAP_INSTALL_ROOT, $env:SIDETAP_NO_LAUNCH.

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072  # TLS 1.2

$root = if ($env:SIDETAP_INSTALL_ROOT) { $env:SIDETAP_INSTALL_ROOT } else { Join-Path $env:LOCALAPPDATA 'SideTap' }
$app = Join-Path $root 'app'
$bin = Join-Path $root 'bin'
New-Item -ItemType Directory -Force -Path $root, $bin | Out-Null

function Step([string]$msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

# ---- 1/6 Python ------------------------------------------------------------
# `python` can resolve to the Microsoft Store stub, which is not Python: it
# opens the Store and exits non-zero. Only trust an exe that answers --version.
function Find-Python {
    $ErrorActionPreference = 'SilentlyContinue'  # the Store stub writes stderr; that must not abort the installer
    foreach ($cand in @('python', 'py')) {
        $cmd = Get-Command $cand -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        $vargs = @('--version'); if ($cand -eq 'py') { $vargs = @('-3', '--version') }
        $out = & $cmd.Source @vargs 2>$null
        if ($LASTEXITCODE -eq 0 -and "$out" -match 'Python 3') { return $cmd.Source }
    }
    $local = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
    if (Test-Path $local) { return $local }
    return $null
}

Step '1/6 Python'
$python = Find-Python
if ($python) {
    Write-Host "    found: $python"
} else {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw 'No Python and no winget. Install Python 3.12 from python.org, then run this again.'
    }
    Write-Host '    installing Python 3.12 (winget)...'
    winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements --override '/quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1' | Out-Null
    $python = Find-Python
    if (-not $python) { throw 'Python install finished but python.exe was not found. Open a NEW terminal and run the installer again.' }
    Write-Host "    installed: $python"
}

# ---- 2/6 SideTap app -------------------------------------------------------
Step "2/6 SideTap app -> $app"
$zip = Join-Path $root 'sidetap.zip'
Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/ucsandman/SideTap/archive/refs/heads/main.zip' -OutFile $zip
$extract = Join-Path $root '_extract'
if (Test-Path $extract) { Remove-Item -Recurse -Force $extract }
Expand-Archive -Path $zip -DestinationPath $extract
$inner = Get-ChildItem -Path $extract -Directory | Select-Object -First 1
# Update = rename-first. Renaming a folder a process is running from fails
# BEFORE anything is touched (Windows treats it as locked), so a running
# SideTap aborts the update cleanly instead of being half-deleted — the
# Remove-Item this replaces gutted a live install on the first clean-machine
# test (2026-08-13), .env included.
$old = Join-Path $root 'app.old'
if (Test-Path $app) {
    if (Test-Path $old) { Remove-Item -Recurse -Force $old }
    try {
        Move-Item $app $old -ErrorAction Stop
    } catch {
        Remove-Item -Recurse -Force $extract
        Remove-Item -Force $zip
        throw 'SideTap is running, so its folder is locked. Close the SideTap console window, then run this installer again. Nothing was changed.'
    }
}
Move-Item $inner.FullName $app
# Carry what the user/setup already earned across the update. _keep first (a
# rescue stash left by installers before the rename-first fix), then app.old
# (the just-renamed install — the current truth, so it wins on overlap).
foreach ($stash in @((Join-Path $root '_keep'), $old)) {
    if (-not (Test-Path $stash)) { continue }
    # '.state*' also carries a Pro fleet's per-phone dirs (.state-b, ...).
    $keep = @('.env', 'wda') + @(Get-ChildItem $stash -Directory -Filter '.state*' -Force -ErrorAction SilentlyContinue | ForEach-Object Name)
    foreach ($p in $keep) {
        $src = Join-Path $stash $p
        if (Test-Path $src) { Copy-Item -Recurse -Force $src (Join-Path $app $p) }
    }
    Remove-Item -Recurse -Force $stash -ErrorAction SilentlyContinue
}
Remove-Item -Recurse -Force $extract
Remove-Item -Force $zip
Write-Host '    done'

# ---- 3/6 Python packages ---------------------------------------------------
Step '3/6 Python packages (pip)'
& $python -m pip install -q --disable-pip-version-check -r (Join-Path $app 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'pip install failed. Read the message above.' }
Write-Host '    done'

# ---- 4/6 go-ios ------------------------------------------------------------
Step '4/6 go-ios (USB phone tool)'
$existing = Get-Command ios -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "    found: $($existing.Source)"
} else {
    $gzip = Join-Path $root 'go-ios-win.zip'
    Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/danielpaulus/go-ios/releases/latest/download/go-ios-win.zip' -OutFile $gzip
    Expand-Archive -Path $gzip -DestinationPath $bin -Force
    Remove-Item -Force $gzip
    if (-not (Test-Path (Join-Path $bin 'ios.exe'))) { throw "go-ios download did not contain ios.exe (looked in $bin)." }
    # On the user PATH for terminals; SideTap itself also knows this folder.
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if (($userPath -split ';') -notcontains $bin) {
        [Environment]::SetEnvironmentVariable('Path', ($userPath.TrimEnd(';') + ';' + $bin), 'User')
    }
    $env:Path += ';' + $bin
    Write-Host "    installed: $(Join-Path $bin 'ios.exe')"
}

# ---- 5/6 Apple USB driver --------------------------------------------------
# Without this app Windows has no iPhone USB driver, go-ios sees nothing, and
# SideTap sits on "FAIL: No iPhone found over USB" forever. So this step never
# trusts winget's exit code: it re-checks that the app actually landed, and on
# failure it opens the Store page itself instead of printing a link that
# scrolls away when SideTap launches (that exact miss shipped once, 2026-08-13).
function Test-AppleDriver {
    [bool]((Get-Service -Name 'Apple Mobile Device Service' -ErrorAction SilentlyContinue) -or
           (Get-AppxPackage -Name 'AppleInc.AppleDevices*' -ErrorAction SilentlyContinue) -or
           (Get-AppxPackage -Name 'AppleInc.iTunes*' -ErrorAction SilentlyContinue))
}
Step '5/6 Apple Devices app (USB driver)'
if (Test-AppleDriver) {
    Write-Host '    found'
} else {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host '    installing Apple Devices (Microsoft Store)...'
        winget install -e --id 9NP83LWLPZ9K -s msstore --accept-package-agreements --accept-source-agreements | Out-Null
    }
    if (Test-AppleDriver) {
        Write-Host '    installed'
    } else {
        Write-Host '    Could not install it automatically. Opening the Microsoft Store page --' -ForegroundColor Yellow
        Write-Host '    click Get there (free, made by Apple). SideTap cannot see your iPhone without it.' -ForegroundColor Yellow
        Write-Host '    Link if the Store did not open: https://apps.microsoft.com/detail/9np83lwlpz9k' -ForegroundColor Yellow
        Start-Process 'ms-windows-store://pdp/?ProductId=9NP83LWLPZ9K' -ErrorAction SilentlyContinue
    }
}

# ---- 6/6 Shortcut ----------------------------------------------------------
Step '6/6 Desktop + Start Menu shortcut'
powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $app 'scripts\install_shortcut.ps1')

# ---- Done ------------------------------------------------------------------
Write-Host ''
Write-Host 'SideTap is installed.' -ForegroundColor Green
Write-Host ''
if (-not (Test-AppleDriver)) {
    Write-Host 'ONE THING IS STILL MISSING: the Apple Devices app (the USB driver).' -ForegroundColor Red
    Write-Host 'Until you install it, SideTap will say "No iPhone found over USB".' -ForegroundColor Red
    Write-Host '  1. Microsoft Store -> search "Apple Devices" (free, made by Apple) -> Get' -ForegroundColor Red
    Write-Host '     or: https://apps.microsoft.com/detail/9np83lwlpz9k' -ForegroundColor Red
    Write-Host '  2. Replug the iPhone, unlock it, tap Trust.' -ForegroundColor Red
    Write-Host ''
}
Write-Host 'Apple makes you do the last steps yourself (about 10 minutes, once):'
Write-Host '  1. Plug in your iPhone. Unlock it. Tap Trust.'
Write-Host '  2. The SideTap window that opens next walks you through the rest'
Write-Host '     (Developer Mode, WebDriverAgent via Sideloadly).'
Write-Host ''
if (-not $env:SIDETAP_NO_LAUNCH) {
    Write-Host 'Starting SideTap...'
    Start-Process -FilePath (Join-Path $app 'sidetap.cmd') -WorkingDirectory $app
} else {
    Write-Host "Start it any time: the SideTap shortcut on your Desktop."
}
