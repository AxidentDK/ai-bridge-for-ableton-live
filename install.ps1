<#
    AI Bridge for Ableton Live — one-line installer (Windows).

        irm https://raw.githubusercontent.com/AxidentDK/ai-bridge-for-ableton-live/main/install.ps1 | iex

    Downloads the bridge, installs it into Ableton Live, and puts a Gemini Studio icon on
    your desktop. No admin rights: everything lands under your own user profile.

    WHY A SEPARATE SCRIPT FROM install.py. install.py assumes you already have the files —
    it copies the remote script into Live's User Library. This is the step before that:
    getting the files at all, on a machine where the only thing you have is a browser. The
    two are kept apart so neither has to guess which situation it is in, and so anyone who
    would rather clone the repo can skip this entirely and run install.py directly.

    It is safe to run again: an existing install is replaced, and your API key is never
    touched (it lives in ~/.ai-bridge and is deliberately outside everything this writes).
#>

$ErrorActionPreference = 'Stop'

$Repo        = 'AxidentDK/ai-bridge-for-ableton-live'
$Branch      = 'main'
$AppName     = 'AI Bridge for Ableton Live'
$InstallRoot = Join-Path $env:LOCALAPPDATA 'AI Bridge'
$ZipUrl      = "https://github.com/$Repo/archive/refs/heads/$Branch.zip"

function Say  ($m) { Write-Host $m }
function Step ($m) { Write-Host "`n== $m" -ForegroundColor Cyan }
function Good ($m) { Write-Host "   $m" -ForegroundColor Green }
function Warn ($m) { Write-Host "   $m" -ForegroundColor Yellow }
function Die  ($m) { Write-Host "`n$m" -ForegroundColor Red; exit 1 }

Say ""
Say "  $AppName"
Say "  Let an AI compose, mix, automate and render inside Ableton Live."
Say ""

# --- 1. Python -----------------------------------------------------------------------
# Checked FIRST and never installed automatically. Silently putting a language runtime on
# someone's machine is not a thing an installer should decide, and a wrong Python is worse
# than none: Live's own Python is not involved here and must not be picked up by accident.
Step "Looking for Python"
$py = $null
foreach ($candidate in @('python', 'python3', 'py')) {
    $found = Get-Command $candidate -ErrorAction SilentlyContinue
    if (-not $found) { continue }
    try { $v = & $found.Source -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null }
    catch { continue }
    if ($v -and [version]$v -ge [version]'3.10') { $py = $found.Source; $pyVersion = $v; break }
}
if (-not $py) {
    Die @"
Python 3.10 or newer is required and was not found.

  Install it from  https://www.python.org/downloads/
  On the first screen, tick "Add python.exe to PATH".

Then run this installer again. Nothing has been changed.
"@
}
Good "Python $pyVersion  ($py)"

# --- 2. Download ---------------------------------------------------------------------
Step "Downloading the bridge"
$tempZip = Join-Path ([System.IO.Path]::GetTempPath()) "ai-bridge-$([guid]::NewGuid()).zip"
$tempOut = Join-Path ([System.IO.Path]::GetTempPath()) "ai-bridge-$([guid]::NewGuid())"
try {
    Invoke-WebRequest -Uri $ZipUrl -OutFile $tempZip -UseBasicParsing
} catch {
    Die "Could not download from GitHub.`n  $($_.Exception.Message)`n`nCheck your connection, or download the repository as a ZIP by hand and run install.py inside it."
}
Expand-Archive -Path $tempZip -DestinationPath $tempOut -Force
$extracted = Get-ChildItem $tempOut -Directory | Select-Object -First 1
if (-not $extracted) { Die "The download did not contain what was expected." }
Good "downloaded"

# --- 3. Put it in place --------------------------------------------------------------
# Under LOCALAPPDATA, so no admin rights and no Program Files. The old copy is removed
# rather than merged: a half-replaced install is the kind of thing that fails weeks later
# with a traceback nobody can explain.
Step "Installing to $InstallRoot"
$target = Join-Path $InstallRoot 'ai-bridge-for-ableton-live'
if (Test-Path $target) {
    Say "   replacing the previous install"
    Remove-Item $target -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
Move-Item $extracted.FullName $target
Remove-Item $tempZip, $tempOut -Recurse -Force -ErrorAction SilentlyContinue
Good "files in place"

# --- 4. Install into Live ------------------------------------------------------------
Step "Installing the Control Surface into Ableton Live"
Push-Location $target
try {
    & $py 'install.py'
    if ($LASTEXITCODE -ne 0) { throw "install.py exited with $LASTEXITCODE" }
} catch {
    Pop-Location
    Die "The bridge was downloaded to`n  $target`nbut installing it into Live failed:`n  $($_.Exception.Message)`n`nYou can run it by hand:  python `"$target\install.py`""
} finally {
    if ((Get-Location).Path -eq $target) { Pop-Location }
}

# --- 5. Desktop icon -----------------------------------------------------------------
# pythonw.exe, not python.exe: the console flash of a windowed app looks like something
# went wrong, and there is nothing to read in it.
Step "Creating the Gemini Studio icon"
$pythonw = Join-Path (Split-Path $py -Parent) 'pythonw.exe'
if (-not (Test-Path $pythonw)) { $pythonw = $py }
$studio  = Join-Path $target 'tools\gemini_studio.py'
# Two icons ship — a rendered one and a vector one; see assets/README.md. The rendered one
# is the default, and the fallback means an icon-less install still gets a working
# shortcut rather than failing over decoration.
$iconSrc = Join-Path $target 'assets\ai-bridge-lit.ico'
if (-not (Test-Path $iconSrc)) { $iconSrc = Join-Path $target 'assets\ai-bridge-flat.ico' }

$desktop  = [Environment]::GetFolderPath('Desktop')
$linkPath = Join-Path $desktop 'Gemini Studio (Ableton).lnk'
$shell    = New-Object -ComObject WScript.Shell
$link     = $shell.CreateShortcut($linkPath)
$link.TargetPath       = $pythonw
$link.Arguments        = "`"$studio`""
$link.WorkingDirectory = $target
$link.Description      = 'Chat with Gemini and let it work inside Ableton Live'
if (Test-Path $iconSrc) { $link.IconLocation = $iconSrc }
$link.Save()
Good "`"$linkPath`""

# --- 6. What is left for a human -----------------------------------------------------
Say ""
Say "  Done. Two things only you can do:" -ForegroundColor Green
Say ""
Say "  1. In Ableton Live:  Preferences -> Link, Tempo & MIDI -> Control Surface"
Say "     Pick 'AI Bridge' in an empty slot. Leave Input and Output on 'None'."
Say "     Restart Live if it was already running - it reads that list at startup."
Say ""
Say "  2. Double-click 'Gemini Studio (Ableton)' on your desktop, then"
Say "     Settings -> Gemini API key. The dialog links to a free key."
Say ""
Say "  Using Claude instead of Gemini? You do not need the Studio window - register"
Say "  the bridge as an MCP server and use the Claude desktop app:"
Say ""
Say "     claude mcp add ai-bridge -- `"$py`" `"$target\host\mcp_server.py`""
Say ""
Say "  Installed at: $target"
Say ""
