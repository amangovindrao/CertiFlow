; CertiFlow.iss
; -------------
; Inno Setup script that turns the PyInstaller folder build into a single
; shareable, installable setup program.
;
; Do not run this directly - package.py drives it, passing the version and
; paths in, and verifying first that no personal data ended up in the payload:
;
;     python tools/package.py
;
; Design notes
; ------------
; * Per-user install by default (PrivilegesRequired=lowest), so a colleague can
;   install it without an administrator password. Admins may still choose a
;   machine-wide install; PrivilegesRequiredOverridesAllowed=dialog asks.
; * User data is NOT stored in the program folder. installed.marker tells
;   settings.py to use %LOCALAPPDATA%\CertiFlow instead, which stays writable
;   under Program Files and survives upgrades and uninstalls.
; * Uninstalling deliberately leaves that data behind. Losing a company's
;   certificate records to a stray uninstall would be unrecoverable, so the
;   uninstaller asks before removing anything.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\CertiFlow"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist\installer"
#endif

#define AppName        "CertiFlow"
#define AppPublisher   "ScaleOn"
#define AppExeName     "CertiFlow.exe"
#define AppId          "{{7C4E2A56-93B1-4E2F-9A17-5C0D8B6E41A3}"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
VersionInfoVersion={#AppVersion}
VersionInfoCompany={#AppPublisher}
VersionInfoDescription={#AppName} Setup
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
UninstallDisplayName={#AppName} {#AppVersion}
UninstallDisplayIcon={app}\{#AppExeName}
OutputDir={#OutputDir}
OutputBaseFilename={#AppName}-{#AppVersion}-Setup
SetupIconFile=..\assets\app.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; No admin rights needed for the default per-user install; an administrator can
; still pick a machine-wide one.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
; Refuse to install over a running copy rather than leaving a half-updated one.
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; The whole PyInstaller folder build.
Source: "{#SourceDir}\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
; Tells settings.py this is an installed copy, so data goes to LOCALAPPDATA.
Source: "installed.marker"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Written at runtime inside the program folder, so not tracked by the installer.
Type: filesandordirs; Name: "{app}\logs"
Type: dirifempty; Name: "{app}"

[Code]
// Offer to remove the user's records on uninstall, defaulting to keeping them.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\{#AppName}');
    if DirExists(DataDir) then
    begin
      if MsgBox('Also delete your CertiFlow data?' + #13#10#13#10 +
                DataDir + #13#10#13#10 +
                'This holds your company profile, the intern database, saved ' +
                'designs and every generated document.' + #13#10#13#10 +
                'Choose No to keep it - a later reinstall will pick it up ' +
                'again. This cannot be undone.',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
    end;
  end;
end;
