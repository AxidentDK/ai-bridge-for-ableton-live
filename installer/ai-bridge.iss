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

[Files]
Source: "{#Stage}\python\*"; DestDir: "{app}\python"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Stage}\app\*";    DestDir: "{app}\app";    Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Gemini Studio";   Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\app\tools\gemini_studio.py"""; WorkingDir: "{app}\app"; IconFilename: "{app}\app\assets\ai-bridge-lit.ico"; Comment: "Chat with Gemini and let it work inside Ableton Live"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Gemini Studio (Ableton)"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\app\tools\gemini_studio.py"""; WorkingDir: "{app}\app"; IconFilename: "{app}\app\assets\ai-bridge-lit.ico"; Comment: "Chat with Gemini and let it work inside Ableton Live"; Tasks: desktopicon

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\app\tools\gemini_studio.py"""; WorkingDir: "{app}\app"; Description: "Open Gemini Studio now"; Flags: postinstall nowait skipifsilent unchecked

[UninstallRun]
; Take the Control Surface back out of Live's User Library. Same script, same logic as
; install — one source of truth for where the files go.
Filename: "{app}\python\python.exe"; Parameters: """{app}\app\install.py"" --uninstall"; WorkingDir: "{app}\app"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveControlSurface"

[UninstallDelete]
; __pycache__ is created at run time, so Inno does not know about it.
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\python"
Type: dirifempty;     Name: "{app}"

[Messages]
FinishedLabel=The bridge is installed.%n%nOne step only you can do, in Ableton Live:%n  Preferences > Link, Tempo & MIDI > Control Surface%n  pick "AI Bridge" in an empty slot, Input and Output on "None".%n  Restart Live if it was running.%n%nThen open Gemini Studio and add your API key under Settings.

[Code]
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
        'The bridge is installed, but it could not be added to Ableton Live automatically.' + #13#10#13#10 +
        'Usually this means Live''s User Library was not found in its standard place.' + #13#10 +
        'Open Live once, then run this from PowerShell (with your own path if Live keeps' + #13#10 +
        'its User Library somewhere else):' + #13#10#13#10 +
        '  "' + ExpandConstant('{app}\python\python.exe') + '" "' +
        ExpandConstant('{app}\app\install.py') + '" --user-library "<path to your User Library>"',
        mbError, MB_OK, IDOK);
    end;
  end;
end;
