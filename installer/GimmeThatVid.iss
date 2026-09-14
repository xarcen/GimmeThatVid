; Inno Setup script: wraps the frozen app into GimmeThatVid-Setup.exe.
; Built by tools/build_installer.py, which passes AppVersion, SourceDir, OutputDir and IconFile.

#define AppName "GimmeThatVid"
#define AppExe "GimmeThatVid.exe"
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
; Never change AppId: it's how Windows recognises updates to the same app.
AppId={{8F3D0C9B-7E21-4B6A-9C55-2D1E6A4F0B77}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName}
AppPublisher={#AppName}
VersionInfoVersion={#AppVersion}

; Per-user install into %LOCALAPPDATA%\Programs: no admin password needed.
PrivilegesRequired=lowest
DefaultDirName={autopf}\{#AppName}
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
UsePreviousTasks=yes

OutputDir={#OutputDir}
OutputBaseFilename={#AppName}-Setup
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}

WizardStyle=modern
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
; Closes a running GimmeThatVid when installing an update over it.
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "german"; MessagesFile: "compiler:Languages\German.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; The Start menu entry is what makes the app show up in Windows Search.
; AppUserModelID matches the one the app sets, so pinning and taskbar grouping line up.
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; AppUserModelID: "GimmeThatVid.App"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon; AppUserModelID: "GimmeThatVid.App"

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; The downloaded engine updates. Settings, history and saved videos are left alone.
Type: filesandordirs; Name: "{localappdata}\{#AppName}\engine"

[Code]
const
  WebView2Key = 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  WebView2KeyUser = 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

function WebView2Installed: Boolean;
var
  Version: String;
begin
  Result :=
    (RegQueryStringValue(HKLM, WebView2Key, 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0')) or
    (RegQueryStringValue(HKCU, WebView2KeyUser, 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0'));
end;

function InitializeSetup: Boolean;
begin
  Result := True;
  // Windows 11 always has WebView2; some older Windows 10 PCs don't.
  if not WebView2Installed then
    MsgBox('GimmeThatVid needs Microsoft Edge WebView2, which this PC doesn''t seem to have.' + #13#10#13#10 +
           'Setup will continue. If GimmeThatVid won''t open afterwards, install WebView2 from:' + #13#10 +
           'https://go.microsoft.com/fwlink/p/?LinkId=2124703', mbInformation, MB_OK);
end;
