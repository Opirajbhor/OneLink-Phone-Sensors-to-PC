; OneLink installer — builds with:  ISCC.exe installer.iss
; Per-user install (no admin prompt), tray autostart optional.

#define AppName "OneLink"
#define AppVersion "1.6"
#define AppExe "OneLink.exe"

[Setup]
AppId={{9F2B41C7-3C1E-4E7B-9A56-6C0F2D9E51A4}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=OneLink
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=E:\Coding\BTBridge\release
OutputBaseFilename=OneLink-Setup-{#AppVersion}
SetupIconFile=E:\Coding\BTBridge\pc\onelink\webui\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Tasks]
Name: "autostart"; Description: "Start OneLink when I sign in (stays in the tray)"; GroupDescription: "Startup:"
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "E:\Coding\BTBridge\release\app\OneLink\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; \
    ValueName: "OneLink"; ValueData: """{app}\{#AppExe}"""; Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#AppExe}"; Description: "Start OneLink now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/c taskkill /im {#AppExe} /f"; Flags: runhidden; RunOnceId: "KillOneLink"
