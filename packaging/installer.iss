; Inno Setup script — wraps dist\ORION into a proper Windows installer (Start menu, uninstaller, optional autostart).
;   iscc /DAppVersion=2.0.0 packaging\installer.iss
#ifndef AppVersion
  #define AppVersion "2.0.0"
#endif

[Setup]
AppId={{6F4C2A61-5B7E-4E0B-9D2E-0A7C1B8E2F10}
AppName=ORION
AppVersion={#AppVersion}
AppPublisher=ORION
DefaultDirName={localappdata}\Programs\ORION
DefaultGroupName=ORION
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=ORION-Setup-{#AppVersion}
SetupIconFile=..\orion\assets\orion.ico
UninstallDisplayIcon={app}\ORION.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; Flags: unchecked
Name: "autostart"; Description: "Start ORION when Windows starts (runs quietly in the tray)"; Flags: unchecked

[Files]
Source: "..\dist\ORION\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\ORION"; Filename: "{app}\ORION.exe"
Name: "{autodesktop}\ORION"; Filename: "{app}\ORION.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "ORION"; \
  ValueData: """{app}\ORION.exe"" --minimized"; Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "{app}\ORION.exe"; Description: "Launch ORION"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "taskkill"; Parameters: "/IM ORION.exe /F"; Flags: runhidden; RunOnceId: "KillORION"
