"""Túnel de acesso externo à API (um Maestro orquestrando outro pela internet).

Backends, em ordem de preferência:
1. cloudflared (grátis, sem conta: URL https://*.trycloudflare.com) — instala via winget se faltar;
2. ngrok, se o usuário já tiver instalado/configurado (usa o authtoken dele).

Segurança: requisições vindas do túnel chegam com X-Forwarded-For e são exigidas
com o token de API (ver middleware em api.py). Local continua livre.
"""
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.request

from . import config, db

_state = {
    "proc": None,        # subprocess.Popen
    "url": "",
    "backend": "",
    "error": "",
    "log": [],
}
_lock = threading.Lock()

_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com|https://[a-z0-9-]+\.ngrok[a-z.-]*\.(?:app|dev|io)")


_CLOUDFLARED_URL = ("https://github.com/cloudflare/cloudflared/releases/latest/"
                    "download/cloudflared-windows-amd64.exe")
_LOCAL_BIN = os.path.join(config.DATA_DIR, "bin", "cloudflared.exe")


def _find_cloudflared() -> str | None:
    if os.path.isfile(_LOCAL_BIN):
        return _LOCAL_BIN
    found = shutil.which("cloudflared")
    if found:
        return found
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("LOCALAPPDATA", "")):
        for cand in (os.path.join(base, "cloudflared", "cloudflared.exe"),
                     os.path.join(base, "Microsoft", "WinGet", "Links", "cloudflared.exe")):
            if cand and os.path.isfile(cand):
                return cand
    return None


def _install_cloudflared() -> str | None:
    """Baixa o binário estático do GitHub (sem winget/MSI, que falham com
    outro instalador em andamento — erro 1618)."""
    try:
        os.makedirs(os.path.dirname(_LOCAL_BIN), exist_ok=True)
        tmp = _LOCAL_BIN + ".part"
        with urllib.request.urlopen(_CLOUDFLARED_URL, timeout=120) as resp, open(tmp, "wb") as f:
            shutil.copyfileobj(resp, f)
        os.replace(tmp, _LOCAL_BIN)
    except OSError:
        return None
    return _find_cloudflared()


def status() -> dict:
    with _lock:
        proc = _state["proc"]
        running = bool(proc and proc.poll() is None)
        return {
            "running": running,
            "url": _state["url"] if running else "",
            "backend": _state["backend"] if running else "",
            "error": _state["error"],
            "api_token": config.get_setting("api_token"),
        }


def start(port: int) -> dict:
    """Sobe o túnel e espera a URL pública (até ~30s)."""
    with _lock:
        if _state["proc"] and _state["proc"].poll() is None:
            return status()
        _state.update(url="", backend="", error="", log=[])

    exe = _find_cloudflared()
    backend = "cloudflared"
    if not exe:
        exe = _install_cloudflared()
    if not exe:
        ngrok = shutil.which("ngrok")
        if ngrok:
            exe, backend = ngrok, "ngrok"
        else:
            with _lock:
                _state["error"] = ("Não foi possível instalar o cloudflared "
                                   "(nem encontrar o ngrok). Verifique a internet.")
            return status()

    if backend == "cloudflared":
        cmd = [exe, "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate"]
    else:
        cmd = [exe, "http", str(port), "--log", "stdout", "--log-format", "logfmt"]

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=config.CREATE_NO_WINDOW,
        )
    except OSError as e:
        with _lock:
            _state["error"] = f"Falha ao iniciar o túnel: {e}"
        return status()

    with _lock:
        _state["proc"] = proc
        _state["backend"] = backend

    def _reader():
        for line in proc.stdout or []:
            with _lock:
                _state["log"].append(line.rstrip()[:300])
                _state["log"] = _state["log"][-80:]
                if not _state["url"]:
                    m = _URL_RE.search(line)
                    if m:
                        _state["url"] = m.group(0)
                        db.add_event(f"Túnel externo ativo ({backend}): {_state['url']}",
                                     level="warn")

    threading.Thread(target=_reader, daemon=True).start()

    for _ in range(60):  # espera a URL aparecer
        time.sleep(0.5)
        with _lock:
            if _state["url"]:
                break
            if proc.poll() is not None:
                _state["error"] = "O túnel encerrou antes de gerar a URL:\n" + "\n".join(_state["log"][-6:])
                _state["proc"] = None
                break
    else:
        with _lock:
            _state["error"] = "Tempo esgotado aguardando a URL do túnel."
    return status()


def stop() -> dict:
    with _lock:
        proc = _state["proc"]
        _state["proc"] = None
        _state["url"] = ""
    if proc and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            try:
                proc.kill()
            except OSError:
                pass
        db.add_event("Túnel externo encerrado.", level="info")
    return status()
