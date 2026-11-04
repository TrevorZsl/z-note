; z-note 安装包脚本，编译前先生成包含内置字体的 dist\z-note.exe：
;   powershell -ExecutionPolicy Bypass -File scripts\package.ps1 -Installer
; AppVersion 由打包脚本从 version.py 读取并传入，禁止手工维护第二份版本号。
#ifndef AppVersion
  #error "Build with scripts/package.ps1 -Installer"
#endif

[Setup]
AppId={{A7DD33A3-341C-4C50-8E9D-6CFC1FB330FC}
AppName=z-note
AppVersion={#AppVersion}
AppMutex=z-note-singleton-mutex
UninstallDisplayName=z-note 便签
DefaultDirName={localappdata}\Programs\z-note
PrivilegesRequired=lowest
UsePreviousAppDir=yes
CloseApplications=no
RestartApplications=no
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=z-note-setup-{#AppVersion}
SetupIconFile=..\assets\icons\app.ico
UninstallDisplayIcon={app}\z-note.exe

[Languages]
Name: "chinesesimplified"; MessagesFile: "Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\z-note.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\z-note"; Filename: "{app}\z-note.exe"
Name: "{autodesktop}\z-note"; Filename: "{app}\z-note.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\z-note.exe"; Description: "{cm:LaunchProgram,z-note}"; Flags: nowait postinstall skipifsilent runascurrentuser; BeforeInstall: PrepareAppLaunch

[Code]
function SetEnvironmentVariable(lpName, lpValue: String): BOOL;
  external 'SetEnvironmentVariableW@kernel32.dll stdcall';

procedure PrepareAppLaunch;
begin
  // Older clients pass their PyInstaller environment through Setup as well.
  // Force a fresh extraction after the old process has removed its _MEI folder.
  if not SetEnvironmentVariable('PYINSTALLER_RESET_ENVIRONMENT', '1') then
    RaiseException('Unable to reset the application launch environment.');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  // 卸载时清掉开机自启项，避免残留指向已删除 exe 的注册表值
  if CurUninstallStep = usUninstall then
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'z-note');
end;
