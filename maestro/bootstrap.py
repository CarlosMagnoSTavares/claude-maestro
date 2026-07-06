"""Bootstrap: detecta/instala Claude Code, bypass, login e CLAUDE.md do projeto."""
import os
import json
import shutil
import subprocess

from . import claude_cli, config


def _claude_config_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".claude.json")


# Caminhos padrão do Node no Windows — o PATH do processo não atualiza após uma
# instalação feita com o app aberto, então procuramos também nos locais fixos.
_NODE_DIRS = [
    os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "nodejs"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "nodejs"),
]


def find_node_tool(name: str) -> str | None:
    """Localiza node/npm/npx mesmo logo após a instalação (PATH ainda antigo)."""
    found = shutil.which(name) or shutil.which(name + ".cmd")
    if found:
        return found
    for d in _NODE_DIRS:
        for cand in (os.path.join(d, name + ".cmd"), os.path.join(d, name + ".exe")):
            if os.path.isfile(cand):
                return cand
    return None


def install_node() -> dict:
    """Instala o Node.js LTS via winget (silencioso). Necessário só para as skills."""
    if find_node_tool("npm"):
        return {"ok": True, "output": "Node.js já instalado."}
    winget = shutil.which("winget")
    if not winget:
        return {"ok": False,
                "output": "winget não disponível. Instale o Node.js LTS manualmente em https://nodejs.org."}
    try:
        proc = subprocess.run(
            [winget, "install", "-e", "--id", "OpenJS.NodeJS.LTS",
             "--silent", "--accept-source-agreements", "--accept-package-agreements",
             "--disable-interactivity"],
            capture_output=True, text=True, timeout=900,
            encoding="utf-8", errors="replace",
            creationflags=config.CREATE_NO_WINDOW,
        )
        ok = bool(find_node_tool("npm")) or proc.returncode == 0
        out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        return {"ok": ok, "output": out[-2000:]}
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "output": f"Falha ao instalar Node.js: {e}"}


def status() -> dict:
    """Diagnóstico completo do ambiente para a tela de instalação."""
    node = find_node_tool("node")
    npm = find_node_tool("npm")
    claude_exe = claude_cli.find_claude()
    version = claude_cli.claude_version() if claude_exe else None

    cfg_path = _claude_config_path()
    cfg_exists = os.path.isfile(cfg_path)
    bypass = False
    logged_in = False
    if cfg_exists:
        try:
            with open(cfg_path, encoding="utf-8", errors="replace") as f:
                raw = f.read()
            cfg = json.loads(raw)
            bypass = bool(cfg.get("bypassPermissionsModeAccepted"))
            logged_in = bool(cfg.get("oauthAccount")) or '"oauthAccount"' in raw
        except (OSError, json.JSONDecodeError):
            pass

    ready = bool(claude_exe and version and logged_in)
    return {
        "node_ok": bool(node),
        "npm_ok": bool(npm),
        "claude_installed": bool(claude_exe and version),
        "claude_version": version or "",
        "claude_path": claude_exe or "",
        "config_exists": cfg_exists,
        "bypass_accepted": bypass,
        "logged_in": logged_in,
        "ready": ready,
    }


def install_claude() -> dict:
    """Instala o Claude Code: instalador nativo (sem Node) com fallback npm."""
    # 1) Instalador nativo oficial (PowerShell) — não exige Node.js
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
             "irm https://claude.ai/install.ps1 | iex"],
            capture_output=True, text=True, timeout=900,
            encoding="utf-8", errors="replace",
            creationflags=config.CREATE_NO_WINDOW,
        )
        if proc.returncode == 0 and claude_cli.find_claude():
            out = (proc.stdout or "") + "\n" + (proc.stderr or "")
            return {"ok": True, "output": out.strip()[-3000:]}
        native_out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        native_out = f"Instalador nativo falhou: {e}"

    # 2) Fallback: npm global (se o Node.js existir)
    npm = find_node_tool("npm")
    if not npm:
        return {"ok": False,
                "output": ("Instalador nativo falhou e Node.js/npm não foi encontrado.\n"
                           "Verifique sua conexão com a internet e tente novamente.\n\n"
                           + native_out[-1500:])}
    try:
        proc = subprocess.run(
            ["cmd", "/c", npm, "install", "-g", "@anthropic-ai/claude-code"],
            capture_output=True, text=True, timeout=600,
            encoding="utf-8", errors="replace",
            creationflags=config.CREATE_NO_WINDOW,
        )
        ok = proc.returncode == 0 and bool(claude_cli.find_claude())
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        return {"ok": ok, "output": out.strip()[-3000:]}
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "output": f"Falha na instalação: {e}"}


def enable_bypass() -> dict:
    """Grava a aceitação do modo bypass no ~/.claude.json (best-effort)."""
    path = _claude_config_path()
    try:
        cfg = {}
        if os.path.isfile(path):
            with open(path, encoding="utf-8", errors="replace") as f:
                cfg = json.load(f)
        cfg["bypassPermissionsModeAccepted"] = True
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return {"ok": True}
    except (OSError, json.JSONDecodeError) as e:
        return {"ok": False, "error": str(e)}


def open_login_terminal() -> dict:
    """Abre um terminal para o usuário fazer login no Claude (única etapa manual)."""
    exe = claude_cli.find_claude()
    if not exe:
        return {"ok": False, "error": "Claude Code não instalado."}
    try:
        subprocess.Popen(
            ["cmd", "/c", "start", "Login Claude", "cmd", "/k", exe],
            creationflags=subprocess.CREATE_NEW_CONSOLE if hasattr(subprocess, "CREATE_NEW_CONSOLE") else 0,
        )
        return {"ok": True}
    except OSError as e:
        return {"ok": False, "error": str(e)}


def write_project_claude_md(project: dict, company: dict):
    """Gera/atualiza o CLAUDE.md do projeto com o contexto da empresa (contexto automático em toda execução)."""
    folder = project["folder"]
    os.makedirs(folder, exist_ok=True)
    content = f"""# Contexto do Projeto (gerado pelo Claude Maestro)

## Empresa: {company['name']}
- **Objetivos:** {company.get('objectives') or 'não informado'}
- **Impedimentos:** {company.get('impediments') or 'não informado'}
- **Problemas a resolver:** {company.get('problems') or 'não informado'}

## Projeto: {project['name']}
{project.get('description') or ''}

## Regras de trabalho
- Trabalhe de forma autônoma, sem pedir confirmações ao usuário.
- Ao final de cada tarefa, deixe o resultado funcional e verificado.
- Escreva textos voltados ao usuário em português do Brasil.
"""
    path = os.path.join(folder, "CLAUDE.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path
