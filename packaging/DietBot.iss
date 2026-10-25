#define MyAppName "Porcja"
#define MyAppVersion "0.6.0"
#define MyAppPublisher "krapcys1-maker"
#define MyAppExeName "DietBot.exe"

[Setup]
AppId={{A62F37F9-C1A4-4C98-AB77-E99B18BFE539}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
; Stable install identity/path: upgrades must preserve existing DietBot data and shortcuts.
DefaultDirName={localappdata}\Programs\DietBot
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\release
OutputBaseFilename=Porcja-Setup-{#MyAppVersion}
SetupIconFile=dietbot.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Languages]
Name: "polish"; MessagesFile: "compiler:Languages\Polish.isl"

[Tasks]
Name: "desktopicon"; Description: "Utwórz skrót na pulpicie"; GroupDescription: "Dodatkowe skróty:"; Flags: unchecked

[InstallDelete]
; Skróty z czasów nazwy DietBot (do 0.2.0) — po aktualizacji zostaje tylko „Porcja”.
Type: files; Name: "{autodesktop}\DietBot.lnk"
Type: files; Name: "{autoprograms}\DietBot.lnk"

[Files]
Source: "..\dist\DietBot\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Uruchom {#MyAppName}"; Flags: nowait postinstall skipifsilent
