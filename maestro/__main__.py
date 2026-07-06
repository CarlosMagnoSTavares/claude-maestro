"""Entrada do Maestro: `python -m maestro serve` (UI) ou `python -m maestro run-task <id>` (agendado)."""
import json
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

from . import config


def _free_port(start: int) -> int:
    """Evita conflito de porta: usa a primeira livre a partir de `start`."""
    for port in range(start, start + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start


def _existing_instance() -> str | None:
    """Se já existe um Maestro rodando, retorna a URL dele (instância única)."""
    for port in range(config.DEFAULT_PORT, config.DEFAULT_PORT + 5):
        url = f"http://127.0.0.1:{port}"
        try:
            with urllib.request.urlopen(url + "/api/status", timeout=2) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            if "app_version" in data:
                return url
        except Exception:
            continue
    return None


def _open_window(url: str):
    """Abre a UI como janela de app (Edge) com fallback para o navegador padrão."""
    time.sleep(1.5)
    try:
        subprocess.Popen(["cmd", "/c", "start", "", "msedge", f"--app={url}"],
                         creationflags=config.CREATE_NO_WINDOW)
    except OSError:
        webbrowser.open(url)


def _wait_up(url: str, timeout: float = 15.0):
    """Espera o servidor local responder antes de abrir a janela."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url + "/api/status", timeout=2):
                return True
        except Exception:
            time.sleep(0.3)
    return False


def _run_native_window(url: str) -> bool:
    """Janela nativa (pywebview): a barra de tarefas usa o ícone do Maestro,
    não o do navegador. Retorna True se a janela foi aberta (bloqueia até fechar)."""
    try:
        import webview
    except Exception:
        return False
    try:
        webview.create_window(f"{config.APP_NAME}", url, width=1280, height=860)
        webview.start()   # bloqueia até o usuário fechar a janela
        return True
    except Exception:
        return False


def serve(open_ui: bool = True):
    import uvicorn
    from .api import app  # import direto: funciona também no executável instalado

    existing = _existing_instance()
    if existing:
        # Maestro já está rodando: só abre a janela, não sobe outro servidor
        print(f"\n  {config.APP_NAME} já está rodando em {existing} — abrindo a janela.\n")
        if open_ui:
            _open_window(existing)
        return

    port = _free_port(config.DEFAULT_PORT)
    url = f"http://127.0.0.1:{port}"
    print(f"\n  {config.APP_NAME} v{config.APP_VERSION}")
    print(f"  Interface: {url}\n")

    # log_config=None: o dictConfig padrão do uvicorn exige stdout com isatty()
    # e quebra no executável --windowed (stdout era None; ver installer/launcher.py)
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning", log_config=None))
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    if not open_ui:
        server_thread.join()
        return

    _wait_up(url)
    # janela nativa (ícone do Maestro na barra de tarefas); fecha → encerra o app
    if _run_native_window(url):
        return
    # fallback: navegador em modo app (Edge) e mantém o servidor vivo
    _open_window(url)
    server_thread.join()


def run_task_headless(task_id: int):
    """Execução sem UI (usada pelo Agendador de Tarefas do Windows)."""
    from . import db, orchestrator
    db.init()
    run_id = orchestrator.start_task(task_id)
    print(f"Run {run_id} iniciado para a missão {task_id}. Aguardando…")
    while True:
        run = db.query_one("SELECT status FROM runs WHERE id=?", (run_id,))
        if run and run["status"] not in ("executando", "preparando"):
            print(f"Finalizado: {run['status']}")
            break
        time.sleep(10)


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else "serve"
    if cmd == "serve":
        serve(open_ui="--no-browser" not in args)
    elif cmd == "run-task" and len(args) >= 2:
        run_task_headless(int(args[1]))
    else:
        print("Uso: python -m maestro [serve [--no-browser] | run-task <id>]")
        sys.exit(1)


if __name__ == "__main__":
    main()
