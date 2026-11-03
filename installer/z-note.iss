; z-note 安装包脚本，编译前先用 PyInstaller 生成 dist\z-note：
;   python -m PyInstaller --noconfirm --clean --windowed --onedir --name z-note ^
;     --icon assets\icons\app.ico --add-data "assets\icons;assets\icons" main.py
; 然后编译本脚本：ISCC.exe installer\z-note.iss

[Setup]
AppId={{A7DD33A3-341C-4C50-8E9D-6CFC1FB330FC}
AppName=z-note
AppVersion=1.0.0
UninstallDisplayName=z-note 便签
DefaultDirName={localappdata}\Programs\z-note
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=z-note-setup
SetupIconFile=..\assets\icons\app.ico
UninstallDisplayIcon={app}\z-note.exe

[Languages]
Name: "chinesesimplified"; MessagesFile: "Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\z-note\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\z-note"; Filename: "{app}\z-note.exe"
Name: "{autodesktop}\z-note"; Filename: "{app}\z-note.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\z-note.exe"; Description: "{cm:LaunchProgram,z-note}"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  // 卸载时清掉开机自启项，避免残留指向已删除 exe 的注册表值
  if CurUninstallStep = usUninstall then
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'z-note');
end;
