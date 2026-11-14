; AI Bridge for Ableton Live — Windows installer (Inno Setup 6).
;
; Built by installer\build.ps1, which stages the files and passes AppVersion, Stage and
; OutDir on the command line. Do not compile this file on its own.
;
; WHY A PRIVATE PYTHON. The whole point of a setup.exe over the one-line PowerShell
; installer is that the user does not have to install Python first. So a Python ships
; INSIDE {app}\python: never on PATH, never registered, invisible to any other Python on
; the machine, and removed on uninstall. It is the embeddable CPython plus tkinter,
; because Gemini Studio's window is tkinter and the embeddable build leaves it out.
;
; WHAT IT NEVER TOUCHES: %USERPROFILE%\.ai-bridge, where the Gemini key, the listening
; models and the sound index live. Reinstalling or uninstalling must not cost anyone
; their key or a 20-minute library scan.

#ifndef AppVersion
  #error Build with installer\build.ps1
#endif

#define AppName      "AI Bridge for Ableton Live"
#define AppPublisher "Twistbyte"
#define AppURL       "https://github.com/AxidentDK/ai-bridge-for-ableton-live"

[Setup]
; Fixed forever: this is how Windows recognises an upgrade as the same program.
AppId={{81288ACD-1560-45CC-84B6-DA81829452A7}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
; Per-user, no admin: Live's User Library belongs to the user anyway, and an installer
; that asks for elevation to write into someone's own profile teaches the wrong habit.
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\AI Bridge
DisableDirPage=auto
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
LicenseFile={#Stage}\app\LICENSE
OutputDir={#OutDir}
OutputBaseFilename=AI-Bridge-Setup-{#AppVersion}
SetupIconFile={#Stage}\app\assets\ai-bridge-lit.ico
UninstallDisplayIcon={app}\app\assets\ai-bridge-lit.ico
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Put a Gemini Studio icon on the desktop"; GroupDescription: "Shortcuts:"
; Shown only when Claude Desktop is on this computer; install.py does the merge.
Name: "claudedesktop"; Description: "Connect the Claude desktop app, so Claude can work in Live too"; GroupDescription: "Claude:"; Check: ClaudeDesktopFound

[Files]
Source: "{#Stage}\python\*"; DestDir: "{app}\python"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Stage}\app\*";    DestDir: "{app}\app";    Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Gemini Studio";   Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\app\tools\gemini_studio.py"""; WorkingDir: "{app}\app"; IconFilename: "{app}\app\assets\ai-bridge-lit.ico"; Comment: "Chat with Gemini and let it work inside Ableton Live"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Gemini Studio (Ableton)"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\app\tools\gemini_studio.py"""; WorkingDir: "{app}\app"; IconFilename: "{app}\app\assets\ai-bridge-lit.ico"; Comment: "Chat with Gemini and let it work inside Ableton Live"; Tasks: desktopicon

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\app\tools\gemini_studio.py"""; WorkingDir: "{app}\app"; Description: "Open Gemini Studio now"; Flags: postinstall nowait skipifsilent

[UninstallRun]
; Take the Control Surface back out of Live's User Library, and this copy's entry out of
; Claude Desktop's settings. Same script, same logic as install — one source of truth.
Filename: "{app}\python\python.exe"; Parameters: """{app}\app\install.py"" --uninstall"; WorkingDir: "{app}\app"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveControlSurface"

[UninstallDelete]
; __pycache__ is created at run time, so Inno does not know about it.
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\python"
Type: dirifempty;     Name: "{app}"

[Messages]
FinishedLabel=The bridge is installed.%n%nOne step only you can do, in Ableton Live:%n  Preferences > Link, Tempo & MIDI > Control Surface%n  pick "AI Bridge" in an empty slot, Input and Output on "None".%n  Restart Live if it was running.%n%nThen open Gemini Studio and add your API key under Settings.

[Code]
// Same test as claude_desktop_present() in install.py — keep them alike.
function ClaudeDesktopFound: Boolean;
begin
  Result := DirExists(ExpandConstant('{userappdata}\Claude')) or
            DirExists(ExpandConstant('{localappdata}\Claude-Data')) or
            DirExists(ExpandConstant('{localappdata}\AnthropicClaude')) or
            DirExists(ExpandConstant('{localappdata}\Packages\Claude_pzs8sxrjxfjjc'));
end;

// Claude Desktop writes its own copy of its settings back when it quits, so a change made
// while it runs is made again by a hidden helper (install.py --claude-desktop wait) once it
// has quit. The helper runs on this program's Python, so it holds files here: an update ends
// it first (install.py starts a new one when it is still needed), an uninstall too.
procedure EndClaudeHelpers;
var
  Code: Integer;
begin
  Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
       '-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -like ''*install.py*--claude-desktop wait*'' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"',
       '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  EndClaudeHelpers;
  Result := '';
end;

// Claude Code's own claude.exe is not Claude Desktop: only the desktop app's places count.
function ClaudeDesktopRunning: Boolean;
var
  Code: Integer;
begin
  Result := Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
       '-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "if (Get-Process claude -ErrorAction SilentlyContinue | Where-Object { $_.Path -like ''*\WindowsApps\Claude_*'' -or $_.Path -like ''*\AnthropicClaude\*'' }) { exit 1 } else { exit 0 }"',
       '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 1);
end;

// The uninstall takes the bridge out of Claude Desktop's settings; a running Claude Desktop
// would put it back when it quits, pointing at a removed program. So: quit it first.
function InitializeUninstall: Boolean;
begin
  Result := True;
  if not UninstallSilent then
    while ClaudeDesktopRunning do
      if MsgBox('Quit Claude Desktop first (its tray icon > Quit), then click OK.' + #13#10#13#10 +
                'Cancel stops the uninstall.', mbInformation, MB_OKCANCEL) = IDCANCEL then
      begin
        Result := False;
        Exit;
      end;
  EndClaudeHelpers;
end;

procedure ConnectClaudeDesktop;
var
  ResultCode: Integer;
begin
  WizardForm.StatusLabel.Caption := 'Connecting the Claude desktop app...';
  if not Exec(ExpandConstant('{app}\python\python.exe'),
              '"' + ExpandConstant('{app}\app\install.py') + '" --claude-desktop',
              ExpandConstant('{app}\app'), SW_HIDE, ewWaitUntilTerminated, ResultCode)
     or (ResultCode <> 0) then
    SuppressibleMsgBox(
      'Claude Desktop was not connected: its settings file could not be read,' + #13#10 +
      'so it was left as it was. Gemini Studio and Live are set up.',
      mbInformation, MB_OK, IDOK);
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if (CurPageID = wpFinished) and WizardIsTaskSelected('claudedesktop') then
    WizardForm.FinishedLabel.Caption := WizardForm.FinishedLabel.Caption + #13#10#13#10 +
      'Using Claude: quit Claude Desktop (tray icon > Quit) and start it again.';
end;

// Install the Control Surface into Live via the bundled install.py, and CHECK the result.
// A [Run] entry would ignore the exit code, and the failure that matters here — Live's
// User Library not found — would leave a "successful" install that Live cannot see.
procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    WizardForm.StatusLabel.Caption := 'Installing the Control Surface into Ableton Live...';
    if not Exec(ExpandConstant('{app}\python\python.exe'),
                '"' + ExpandConstant('{app}\app\install.py') + '"',
                ExpandConstant('{app}\app'), SW_HIDE, ewWaitUntilTerminated, ResultCode)
       or (ResultCode <> 0) then
    begin
      SuppressibleMsgBox(
        'The program is installed, but it could not be added to Ableton Live yet:' + #13#10 +
        'Live''s User Library was not found in its usual place.' + #13#10#13#10 +
        'Open Ableton Live once, then run this installer again.' + #13#10#13#10 +
        'If Live keeps its User Library somewhere else (Preferences > Library shows where),' + #13#10 +
        'run this in PowerShell with that path instead:' + #13#10#13#10 +
        '  & "' + ExpandConstant('{app}\python\python.exe') + '" "' +
        ExpandConstant('{app}\app\install.py') + '" --user-library "<path to your User Library>"',
        mbError, MB_OK, IDOK);
    end;
    if WizardIsTaskSelected('claudedesktop') then
      ConnectClaudeDesktop;
  end;
end;
