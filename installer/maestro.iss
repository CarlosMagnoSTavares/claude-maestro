; Instalador do Claude Maestro (Inno Setup 6) — experiência "avançar, avançar, concluir".
; Instala por usuário (sem pedir administrador) em %LOCALAPPDATA%\Programs.

#define MyAppName "Claude Maestro"
#define MyAppVersion "1.5.2"
#define MyAppPublisher "CMST"
#define MyAppExeName "ClaudeMaestro.exe"

[Setup]
AppId={{B7A45C1E-9D2E-4A8B-B7F1-3C67A1D30F42}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=Output
OutputBaseFilename=ClaudeMaestro-Setup-{#MyAppVersion}
SetupIconFile=maestro.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; fechamento do app é feito via taskkill em PrepareToInstall (Restart Manager falhava)
CloseApplications=no
RestartApplications=no

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\ClaudeMaestro\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Code]
// Encerra o Maestro à força antes de copiar arquivos (evita "não consegue fechar" do Restart Manager)
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Result := '';
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM "{#MyAppExeName}"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  // pequena espera para o SO liberar os arquivos
  Sleep(1500);
end;

[Run]
; checkbox "Executar o Claude Maestro" na última tela — já vem MARCADO por padrão
Filename: "{app}\{#MyAppExeName}"; Description: "Executar o {#MyAppName} agora"; Flags: nowait postinstall skipifsilent
