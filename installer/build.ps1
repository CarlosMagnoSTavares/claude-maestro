# Build da release do Claude Maestro para Windows 11.
# Uso:  powershell -ExecutionPolicy Bypass -File installer\build.ps1
# Saída: installer\Output\ClaudeMaestro-Setup-<versao>.exe

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "== 1/4 Dependências de build =="
python -m pip install -q -r requirements.txt pyinstaller pillow

Write-Host "== 2/4 Ícone =="
python installer\gen_icon.py

Write-Host "== 3/4 Executável (PyInstaller, sem console) =="
python -m PyInstaller --noconfirm --clean --onedir --windowed `
  --name ClaudeMaestro `
  --icon "$root\installer\maestro.ico" `
  --paths "$root" `
  --add-data "$root\maestro\static;maestro\static" `
  --add-data "$root\maestro\wa;maestro\wa" `
  --collect-submodules uvicorn `
  --collect-submodules qrcode `
  --collect-all webview `
  --collect-all clr_loader `
  --hidden-import clr `
  --hidden-import multipart `
  --hidden-import PIL.Image `
  --distpath "$root\installer\dist" `
  --workpath "$root\installer\build" `
  --specpath "$root\installer" `
  "$root\installer\launcher.py"

Write-Host "== 3.5/4 Assinatura de codigo (opcional) =="
# Assinatura de codigo (code signing): elimina o bloqueio do SmartScreen/Smart App
# Control no Windows 11. Requer um certificado de assinatura de codigo (comprado -
# ver BUILD.md) apontado pelas variaveis de ambiente CSC_LINK (caminho do .pfx) e
# CSC_KEY_PASSWORD (senha do .pfx). Sem essas variaveis, o build segue normalmente
# SEM assinar (apenas um aviso é exibido) - nao quebra o build atual.
$signtoolCandidates = @(
  "${env:ProgramFiles(x86)}\Windows Kits\10\bin\x64\signtool.exe",
  "${env:ProgramFiles(x86)}\Windows Kits\10\bin\10.0.22621.0\x64\signtool.exe"
)
$signtool = Get-Command signtool.exe -ErrorAction SilentlyContinue
if ($signtool) { $signtool = $signtool.Source } else {
  $signtool = $signtoolCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
  if (-not $signtool) {
    $found = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin" -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
      Where-Object { $_.FullName -match "\\x64\\" } | Select-Object -First 1
    if ($found) { $signtool = $found.FullName }
  }
}

function Sign-Artifact([string]$Path) {
  if (-not $env:CSC_LINK) {
    Write-Host "  CSC_LINK nao definido - $([IO.Path]::GetFileName($Path)) NAO sera assinado (veja BUILD.md)." -ForegroundColor Yellow
    return
  }
  if (-not $signtool) {
    Write-Host "  signtool.exe nao encontrado (instale o Windows SDK) - $([IO.Path]::GetFileName($Path)) NAO sera assinado." -ForegroundColor Yellow
    return
  }
  Write-Host "  Assinando $([IO.Path]::GetFileName($Path)) com $env:CSC_LINK ..."
  & $signtool sign /f "$env:CSC_LINK" /p "$env:CSC_KEY_PASSWORD" /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 "$Path"
  if ($LASTEXITCODE -ne 0) { throw "Falha ao assinar $Path" }
}

Sign-Artifact "$root\installer\dist\ClaudeMaestro\ClaudeMaestro.exe"

Write-Host "== 4/4 Instalador (Inno Setup) =="
$isccCandidates = @(
  "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
  "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
  "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
)
$iscc = $isccCandidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $iscc) {
  Write-Host "Inno Setup 6 não encontrado. Instale com:" -ForegroundColor Yellow
  Write-Host "  winget install -e --id JRSoftware.InnoSetup --scope user" -ForegroundColor Yellow
  Write-Host "O executável ficou em installer\dist\ClaudeMaestro\ClaudeMaestro.exe"
  exit 1
}
& $iscc "$root\installer\maestro.iss"

$setupExe = Get-ChildItem "$root\installer\Output\ClaudeMaestro-Setup-*.exe" -ErrorAction SilentlyContinue |
  Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($setupExe) { Sign-Artifact $setupExe.FullName }

Write-Host ""
Write-Host "Release pronta em installer\Output\" -ForegroundColor Green
if (-not $env:CSC_LINK) {
  Write-Host "AVISO: instalador NAO assinado. SmartScreen/Smart App Control podem bloquear em outros PCs." -ForegroundColor Yellow
  Write-Host "Veja installer\BUILD.md para configurar um certificado de assinatura de codigo." -ForegroundColor Yellow
}
