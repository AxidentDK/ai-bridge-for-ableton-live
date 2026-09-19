<#
    Build the Windows installer:  dist\AI-Bridge-Setup-<version>.exe

        powershell -ExecutionPolicy Bypass -File installer\build.ps1

    Needs Inno Setup 6 and a CPython of the SAME minor version as the bundled one, which
    is where the tkinter files are copied from (the embeddable build leaves tkinter out,
    and _tkinter.pyd must match the interpreter it is loaded into).

    Ships only what git TRACKS, taken with `git archive`, so nothing local — a key file,
    .private-terms, a scratch script — can ride along into a published binary.
#>
param(
    [string]$PyVersion = '3.14.3',
    [string]$FullPython = 'C:\Python314'
)
$ErrorActionPreference = 'Stop'
$Root    = Split-Path $PSScriptRoot -Parent
$Build   = Join-Path $Root 'build\installer'
$Stage   = Join-Path $Build 'stage'
$Cache   = Join-Path $Build 'cache'
$Dist    = Join-Path $Root 'dist'
$Ship    = @('assets', 'docs', 'host', 'm4l', 'remote_script', 'tools',
             'install.py', 'LICENSE', 'NOTICE', 'pyproject.toml', 'README.md')

function Step($m) { Write-Host "`n== $m" -ForegroundColor Cyan }

$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
          "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
          "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw 'Inno Setup 6 not found (ISCC.exe).' }

$version = (Select-String -Path (Join-Path $Root 'pyproject.toml') -Pattern '^version\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
Step "AI Bridge $version  (Python $PyVersion)"

Remove-Item $Stage -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $Stage, $Cache, $Dist | Out-Null

# --- the bridge: tracked files only --------------------------------------------------
Step 'Staging the bridge (git archive of HEAD)'
$dirty = git -C $Root status --porcelain -- $Ship
if ($dirty) { Write-Host "   WARNING: uncommitted changes are NOT included:`n$dirty" -ForegroundColor Yellow }
$zip = Join-Path $Build 'app.zip'
git -C $Root archive --format=zip -o $zip HEAD -- $Ship
if ($LASTEXITCODE -ne 0) { throw 'git archive failed' }
Expand-Archive $zip (Join-Path $Stage 'app') -Force
Remove-Item $zip

# --- Python: embeddable + tkinter ----------------------------------------------------
Step 'Staging a private Python'
$embedName = "python-$PyVersion-embed-amd64.zip"
$embedZip  = Join-Path $Cache $embedName
if (-not (Test-Path $embedZip)) {
    Invoke-WebRequest "https://www.python.org/ftp/python/$PyVersion/$embedName" -OutFile $embedZip -UseBasicParsing
}
$py = Join-Path $Stage 'python'
Expand-Archive $embedZip $py -Force

$full = & "$FullPython\python.exe" -c "import sys; print('%d.%d.%d' % sys.version_info[:3])"
if ($full -ne $PyVersion) {
    throw "tkinter must come from the SAME Python version: bundling $PyVersion but $FullPython is $full."
}
# zlib1.dll is tcl86t.dll's own dependency and the only DLL the embeddable build lacks —
# without it _tkinter fails with a bare "DLL load failed", naming no file.
Copy-Item "$FullPython\DLLs\_tkinter.pyd", "$FullPython\DLLs\tcl86t.dll", "$FullPython\DLLs\tk86t.dll",
          "$FullPython\DLLs\zlib1.dll" $py
New-Item -ItemType Directory -Force -Path "$py\Lib" | Out-Null
Copy-Item "$FullPython\Lib\tkinter" "$py\Lib\tkinter" -Recurse
New-Item -ItemType Directory -Force -Path "$py\tcl" | Out-Null
foreach ($d in 'tcl8', 'tcl8.6', 'tk8.6') { Copy-Item "$FullPython\tcl\$d" "$py\tcl\$d" -Recurse }
Get-ChildItem $py -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force
# Drop Tk's demo programs: 1 MB of examples nobody installing a bridge needs.
Remove-Item "$py\tcl\tk8.6\demos" -Recurse -Force -ErrorAction SilentlyContinue

# An embeddable Python ignores PYTHONPATH and does NOT put the script's own folder on
# sys.path — the ._pth file is the whole search path. So the bridge's folders go in here,
# or every `import mcp_server` from a tool would fail on a user's machine and nowhere else.
$pth = Get-ChildItem $py -Filter 'python*._pth' | Select-Object -First 1
$zipName = (Get-ChildItem $py -Filter 'python*.zip' | Select-Object -First 1).Name
@($zipName, '.', 'Lib', '..\app', '..\app\host', '..\app\tools') | Set-Content $pth.FullName -Encoding ascii

# --- prove the bundle before compiling it --------------------------------------------
Step 'Smoke test of the bundled Python'
$check = @'
import sys, tkinter
r = tkinter.Tk(); r.withdraw(); tcl = r.tk.call('info', 'patchlevel'); r.destroy()
import mcp_server, gemini_studio
print(f"python {sys.version.split()[0]}  tk {tcl}  tools {len(mcp_server.TOOLS)}")
'@
$out = & "$py\python.exe" -c $check 2>&1
if ($LASTEXITCODE -ne 0) { throw "bundled Python failed its smoke test:`n$out" }
Write-Host "   $out" -ForegroundColor Green

# --- compile -------------------------------------------------------------------------
Step 'Compiling with Inno Setup'
& $iscc /Qp "/DAppVersion=$version" "/DStage=$Stage" "/DOutDir=$Dist" (Join-Path $PSScriptRoot 'ai-bridge.iss')
if ($LASTEXITCODE -ne 0) { throw "ISCC failed ($LASTEXITCODE)" }
$exe = Join-Path $Dist "AI-Bridge-Setup-$version.exe"
Write-Host ("`n   {0}  ({1:N1} MB)" -f $exe, ((Get-Item $exe).Length / 1MB)) -ForegroundColor Green
