"""Gateway WhatsApp do Maestro (driver Baileys embutido).

Gerencia o processo Node do bridge (instala dependências no primeiro uso),
mantém o estado da conexão (QR / conectado) e roteia mensagens recebidas
para o bot de menus (wa_bot). Arquitetura plugável: outro driver (ex.:
Evolution API) só precisa expor send/broadcast e chamar wa_bot.handle.
"""
import os
import json
import secrets
import subprocess
import threading

from . import db, config, bootstrap

_lock = threading.Lock()
_proc: subprocess.Popen | None = None
_state = {"status": "desligado", "qr": "", "me": "", "error": ""}


# ------------------------------------------------------------ estado/secret

def get_secret() -> str:
    """Código de autorização de 6 dígitos (criado sob demanda)."""
    s = config.get_setting("wa_secret")
    if not s:
        s = rotate_secret()
    return s


def rotate_secret() -> str:
    s = "".join(secrets.choice("0123456789") for _ in range(6))
    db.set_setting("wa_secret", s)
    db.add_event("Código de autorização do WhatsApp regenerado.", level="warn")
    return s


def status() -> dict:
    with _lock:
        st = dict(_state)
    st["running"] = is_running()
    st["secret"] = get_secret()
    st["operators"] = db.query("SELECT * FROM wa_operators ORDER BY created_at")
    st["settings"] = {k: config.get_setting_bool(k) for k in
                      ("wa_notify_approval", "wa_notify_result", "wa_notify_team")}
    st["enabled"] = config.get_setting_bool("wa_enabled")
    return st


def is_running() -> bool:
    with _lock:
        return bool(_proc and _proc.poll() is None)


# ------------------------------------------------------------ ciclo de vida

def start() -> dict:
    """Sobe o bridge (instala Baileys no primeiro uso). Idempotente."""
    if is_running():
        return {"ok": True, "status": _state["status"]}
    node = bootstrap.find_node_tool("node")
    npm = bootstrap.find_node_tool("npm")
    if not (node and npm):
        return {"ok": False, "error": "Node.js não encontrado. Instale no Painel (Preparar sistema)."}
    with _lock:
        _state.update(status="preparando", qr="", me="", error="")
    threading.Thread(target=_start_worker, args=(node, npm), daemon=True).start()
    return {"ok": True, "status": "preparando"}


def _start_worker(node: str, npm: str):
    global _proc
    config.ensure_wa_dirs()
    try:
        if not _deps_ok():
            with _lock:
                _state["status"] = "instalando"
            r = _npm_install(npm)
            if not r:
                with _lock:
                    _state.update(status="erro",
                                  error="Falha ao instalar o Baileys (npm). Verifique a internet.")
                return
        env = dict(os.environ,
                   WA_SESSION_DIR=config.WA_SESSION_DIR,
                   NODE_PATH=os.path.join(config.WA_RUNTIME_DIR, "node_modules"))
        with _lock:
            _proc = subprocess.Popen(
                [node, config.WA_BRIDGE_JS],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, cwd=config.WA_RUNTIME_DIR, env=env,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=config.CREATE_NO_WINDOW,
            )
            _state["status"] = "conectando"
        db.set_setting("wa_enabled", "true")
        threading.Thread(target=_reader, args=(_proc,), daemon=True).start()
    except OSError as e:
        with _lock:
            _state.update(status="erro", error=str(e))


def _deps_ok() -> bool:
    mods = os.path.join(config.WA_RUNTIME_DIR, "node_modules")
    return (os.path.isdir(os.path.join(mods, "baileys"))
            or os.path.isdir(os.path.join(mods, "@whiskeysockets", "baileys")))


def _npm_install(npm: str) -> bool:
    pkg = os.path.join(config.WA_RUNTIME_DIR, "package.json")
    if not os.path.isfile(pkg):
        with open(pkg, "w", encoding="utf-8") as f:
            json.dump({"name": "maestro-wa", "private": True}, f)
    try:
        proc = subprocess.run(
            ["cmd", "/c", npm, "install", "baileys", "--no-audit", "--no-fund"],
            cwd=config.WA_RUNTIME_DIR, capture_output=True, text=True,
            timeout=600, encoding="utf-8", errors="replace",
            creationflags=config.CREATE_NO_WINDOW)
        if proc.returncode != 0:  # fallback para o nome antigo do pacote
            proc = subprocess.run(
                ["cmd", "/c", npm, "install", "@whiskeysockets/baileys", "--no-audit", "--no-fund"],
                cwd=config.WA_RUNTIME_DIR, capture_output=True, text=True,
                timeout=600, encoding="utf-8", errors="replace",
                creationflags=config.CREATE_NO_WINDOW)
        return _deps_ok()
    except (OSError, subprocess.TimeoutExpired):
        return False


def stop(logout: bool = False):
    """Desliga o bridge; logout=True também desconecta a conta (novo QR depois)."""
    global _proc
    with _lock:
        p = _proc
    if p and p.poll() is None:
        try:
            if logout:
                p.stdin.write(json.dumps({"type": "logout"}) + "\n")
                p.stdin.flush()
                p.wait(timeout=10)
            else:
                p.terminate()
        except (OSError, subprocess.TimeoutExpired):
            p.kill()
    with _lock:
        _proc = None
        _state.update(status="desligado", qr="", me="")
    db.set_setting("wa_enabled", "false")


def autostart():
    """No boot do app: religa o WhatsApp se estava habilitado."""
    if config.get_setting_bool("wa_enabled"):
        start()


# ------------------------------------------------------------ leitura do bridge

def _reader(p: subprocess.Popen):
    from . import wa_bot   # import tardio (evita ciclo)
    for line in iter(p.stdout.readline, ""):
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = ev.get("type")
        if t == "qr":
            with _lock:
                _state.update(status="aguardando_qr", qr=ev.get("data") or "")
        elif t == "status":
            with _lock:
                _state["status"] = ev.get("status") or ""
                _state["me"] = ev.get("me") or _state["me"]
                if _state["status"] == "conectado":
                    _state["qr"] = ""
        elif t == "message":
            phone = ev.get("from") or ""
            text = ev.get("text") or ""
            try:
                for reply in wa_bot.handle(phone, text):
                    send(phone, reply)
            except Exception as e:
                db.add_event(f"Erro no bot do WhatsApp: {e}", level="error")
                send(phone, "⚠️ Ocorreu um erro. Envie *menu* para recomeçar.")
    with _lock:
        if _proc is p:
            _state.update(status="desligado", qr="")


# ------------------------------------------------------------ envio

def send(phone: str, text: str):
    with _lock:
        p = _proc
    if not (p and p.poll() is None):
        return False
    try:
        p.stdin.write(json.dumps({"type": "send", "to": phone, "text": text},
                                 ensure_ascii=False) + "\n")
        p.stdin.flush()
        return True
    except OSError:
        return False


def broadcast(text: str, setting_key: str = ""):
    """Envia para todos os operadores autorizados (respeitando o toggle, se houver)."""
    if setting_key and not config.get_setting_bool(setting_key):
        return
    if not is_running():
        return
    for op in db.query("SELECT phone FROM wa_operators"):
        send(op["phone"], text)


# ------------------------------------------------------------ pushes de domínio

def push_approval(approval_id: int):
    """Aprovação pendente → mensagem com o plano e decisão direto na conversa."""
    if not config.get_setting_bool("wa_notify_approval") or not is_running():
        return
    from . import wa_bot
    text = wa_bot.approval_message(approval_id)
    if not text:
        return
    for op in db.query("SELECT phone FROM wa_operators"):
        wa_bot.set_session(op["phone"], "ap_decide", {"aid": approval_id})
        send(op["phone"], text)


def push_result(task_title: str, status: str, summary: str, cost: float,
                run_id: int | None = None):
    icon = "✅" if status == "concluida" else "❌"
    label = "concluída" if status == "concluida" else "falhou"
    extra = ""
    if status == "concluida" and run_id:
        try:
            from . import deliverables
            lines = deliverables.summary_lines(run_id)
            if lines:
                extra = "\n\n*Entregas na pasta do projeto:*\n" + "\n".join(lines)
        except Exception:
            pass
    broadcast(f"{icon} Missão *{task_title}* {label}.\n{summary}\n"
              f"Custo: US$ {cost:.2f}{extra}", setting_key="wa_notify_result")


def push_team(from_agent: str, to_agent: str, kind: str, content: str):
    icon = "🗒️" if kind == "backlog" else "💬"
    extra = " criou backlog para" if kind == "backlog" else " →"
    broadcast(f"{icon} {from_agent}{extra} {to_agent}: {content[:300]}",
              setting_key="wa_notify_team")
