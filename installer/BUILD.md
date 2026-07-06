# Build e assinatura de código — Claude Maestro

Este projeto **não usa Electron/electron-builder**: o app é Python empacotado com
**PyInstaller** (`installer/ClaudeMaestro.spec`) e o instalador é gerado com
**Inno Setup 6** (`installer/maestro.iss`). O script único de build é
`installer/build.ps1`.

## Por que o instalador é bloqueado em outros PCs

1. **SmartScreen** (Windows Defender) bloqueia executáveis desconhecidos/sem
   assinatura digital com reputação.
2. **Smart App Control** (Windows 11) é mais rígido: só libera app assinado por
   um certificado de editor confiável (ou app já com reputação consolidada na
   nuvem da Microsoft).

A solução técnica para os dois é a mesma: **assinar digitalmente** o
`ClaudeMaestro.exe` e o instalador `ClaudeMaestro-Setup-*.exe` com um
certificado de assinatura de código (code signing certificate).

## Obtendo um certificado (ação humana — compra)

Não é possível gerar um certificado válido sem passar por uma Autoridade
Certificadora (CA). Duas opções:

| Tipo | Custo aprox. | Como libera SmartScreen/Smart App Control |
|---|---|---|
| **EV (Extended Validation)** | mais caro (~US$300-500/ano), exige token USB físico (HSM) e validação de empresa | reputação **imediata** — SmartScreen/Smart App Control liberam desde o primeiro executável assinado |
| **OV (Organization Validation)** | mais barato (~US$70-250/ano), pode ser `.pfx` em arquivo | precisa **construir reputação** aos poucos (Microsoft observa nº de downloads/execuções sem denúncia); nos primeiros dias/semanas o SmartScreen ainda pode alertar |

Fornecedores comuns: [Sectigo](https://www.sectigo.com/), [DigiCert](https://www.digicert.com/),
[SSL.com](https://www.ssl.com/), [GlobalSign](https://www.globalsign.com/).
Para uma empresa pequena que quer eliminar o aviso o quanto antes, **EV é a
escolha mais direta**; OV é aceitável se o time tolerar um período de
"aquecimento" de reputação.

Depois de comprar, a CA entrega um certificado `.pfx` (OV) ou um token USB
(EV, usado via `signtool` apontando para o certificado no token).

## Configurando a assinatura no build

O script `installer/build.ps1` já está preparado para assinar automaticamente
**se** as variáveis de ambiente abaixo estiverem definidas — sem elas, o build
segue normalmente sem assinar (apenas exibe um aviso amarelo no final):

```powershell
$env:CSC_LINK = "C:\caminho\seguro\certificado.pfx"
$env:CSC_KEY_PASSWORD = "senha-do-pfx"
powershell -ExecutionPolicy Bypass -File installer\build.ps1
```

O que acontece internamente:

1. PyInstaller gera `installer\dist\ClaudeMaestro\ClaudeMaestro.exe`.
2. Se `CSC_LINK` estiver definido, o script localiza `signtool.exe` (Windows
   SDK — instale via `winget install Microsoft.WindowsSDK` se necessário) e
   assina o `.exe` com timestamp (`/tr http://timestamp.digicert.com /td SHA256`).
3. Inno Setup compila `ClaudeMaestro-Setup-<versão>.exe`.
4. O instalador final também é assinado com o mesmo certificado.

Certificado em token USB (EV): use o *Subject Name* do certificado em vez do
`.pfx` — troque a linha `signtool sign /f ...` em `installer/build.ps1` por
`/n "Nome exato do editor no certificado"` (o token expõe o certificado no
repositório do Windows automaticamente ao ser conectado).

**Nunca** commitar o `.pfx` ou a senha no repositório — guarde-os fora do
Git (ex.: cofre de senhas, GitHub Actions secrets).

## CI

Este repositório **não possui workflow de CI** (`.github/workflows` não
existe). Quando um pipeline for criado, configure `CSC_LINK` e
`CSC_KEY_PASSWORD` como *secrets* do CI e, no passo de build, decodifique o
certificado (se guardado em base64) para um arquivo temporário antes de rodar
`installer\build.ps1`, por exemplo:

```yaml
- name: Restaurar certificado
  run: |
    [IO.File]::WriteAllBytes("cert.pfx", [Convert]::FromBase64String("${{ secrets.CSC_LINK_BASE64 }}"))
    echo "CSC_LINK=$PWD\cert.pfx" >> $env:GITHUB_ENV
    echo "CSC_KEY_PASSWORD=${{ secrets.CSC_KEY_PASSWORD }}" >> $env:GITHUB_ENV
- run: powershell -ExecutionPolicy Bypass -File installer\build.ps1
```

## Verificando se a assinatura funcionou

```powershell
signtool verify /pa /v installer\Output\ClaudeMaestro-Setup-1.4.0.exe
```

Deve retornar `Successfully verified`. Também é possível checar clicando com
o botão direito no `.exe` → Propriedades → aba "Assinaturas Digitais".
