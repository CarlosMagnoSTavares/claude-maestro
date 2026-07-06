"""Configuração central do Maestro: caminhos absolutos e settings padrão."""
import os
import sys
import json
import threading

FROZEN = bool(getattr(sys, "frozen", False))  # rodando como executável instalado

# App --windowed não tem console: subprocessos de console abririam janelas
# piscando na tela. Usar em TODO subprocess (exceto quando a janela é intencional).
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

if FROZEN:
    # Instalado (PyInstaller): dados do usuário em %LOCALAPPDATA%, estáticos no bundle.
    _local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    APP_ROOT = os.path.join(_local, "ClaudeMaestro")
    _bundle = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    STATIC_DIR = os.path.join(_bundle, "maestro", "static")
else:
    # Raiz do app (pasta que contém o pacote maestro/)
    APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    STATIC_DIR = os.path.join(APP_ROOT, "maestro", "static")

DATA_DIR = os.path.join(APP_ROOT, "data")
# WhatsApp (bridge Baileys embutido)
WA_RUNTIME_DIR = os.path.join(APP_ROOT, "data", "wa_runtime")   # node_modules
WA_SESSION_DIR = os.path.join(APP_ROOT, "data", "wa_session")   # credenciais WA
if FROZEN:
    WA_BRIDGE_JS = os.path.join(_bundle, "maestro", "wa", "baileys_bridge.js")
else:
    WA_BRIDGE_JS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "wa", "baileys_bridge.js")
DB_PATH = os.path.join(DATA_DIR, "maestro.db")
EXPORTS_DIR = os.path.join(DATA_DIR, "exports")
IMPORTS_DIR = os.path.join(DATA_DIR, "imports")
LOGS_DIR = os.path.join(DATA_DIR, "logs")
SEED_COMPANY_JSON = os.path.join(APP_ROOT, "minha_empresa.json")

APP_NAME = "Claude Maestro"
APP_VERSION = "1.5.2"
DEFAULT_PORT = 8765

# Escalas de custo/capacidade (usadas p/ limitar o que a IA pode escolher)
MODEL_ORDER = ["haiku", "sonnet", "opus"]
EFFORT_ORDER = ["low", "medium", "high"]

# Settings padrão (persistidos na tabela settings; estes são os defaults)
DEFAULT_SETTINGS = {
    "model": "sonnet",            # modelo dos agentes executores
    "brain_model": "sonnet",      # modelo do cérebro Maestro (recrutar/planejar)
    "max_concurrency": "2",       # micro-tarefas paralelas simultâneas
    "task_timeout_min": "20",     # timeout por micro-tarefa (minutos)
    "min_installs": "1000",       # reputação mínima de skill (installs em 8 semanas)
    "yolo": "true",               # bypass de permissões (modo YOLO)
    "max_agents": "4",            # máximo de agentes contratados por missão
    "max_turns_exec": "40",       # max turns por micro-tarefa
    "max_turns_brain": "12",      # max turns por chamada do cérebro
    "autonomy_default": "1",      # nível padrão: 1 = aprovação obrigatória (padrão seguro)
    "max_cost_per_run": "5.00",   # budget hard-stop por execução (US$)
    "heartbeat_retry_min": "15",  # fallback do heartbeat quando não dá pra saber o reset
    "max_model": "sonnet",        # teto de modelo que a IA pode escolher p/ agentes
    "max_effort": "high",         # teto de esforço (thinking) por agente
    "proactive_suggestions": "true",  # retrospectiva: equipe sugere próximas missões
    # WhatsApp
    "wa_enabled": "false",           # liga o bridge no boot (fica true após conectar)
    "wa_notify_approval": "true",    # push: aprovação pendente (responder na conversa)
    "wa_notify_result": "true",      # push: missão concluída/falhou
    "wa_notify_team": "true",        # push: conversa/backlog entre agentes
}


def ensure_wa_dirs():
    for d in (WA_RUNTIME_DIR, WA_SESSION_DIR):
        os.makedirs(d, exist_ok=True)


def ensure_dirs():
    for d in (DATA_DIR, EXPORTS_DIR, IMPORTS_DIR, LOGS_DIR):
        os.makedirs(d, exist_ok=True)


# ---- Settings vivos (cache em memória, fonte na tabela settings) ----
_settings_lock = threading.Lock()
_settings_cache: dict = {}


def load_settings_cache(rows: dict):
    with _settings_lock:
        _settings_cache.clear()
        _settings_cache.update(DEFAULT_SETTINGS)
        _settings_cache.update(rows)


def get_setting(key: str) -> str:
    with _settings_lock:
        return _settings_cache.get(key, DEFAULT_SETTINGS.get(key, ""))


def get_setting_int(key: str) -> int:
    try:
        return int(get_setting(key))
    except ValueError:
        return int(DEFAULT_SETTINGS.get(key, "0"))


def get_setting_bool(key: str) -> bool:
    return get_setting(key).strip().lower() in ("1", "true", "sim", "yes", "on")


def set_setting_cache(key: str, value: str):
    with _settings_lock:
        _settings_cache[key] = value


def _clamp(value: str, cap_key: str, order: list[str], default: str) -> str:
    """Limita `value` ao teto configurado em `cap_key` (escala `order`)."""
    v = (value or "").strip().lower()
    cap = get_setting(cap_key).strip().lower()
    if v not in order:
        v = default
    if cap not in order:
        return v
    return v if order.index(v) <= order.index(cap) else cap


def clamp_model(model: str) -> str:
    return _clamp(model, "max_model", MODEL_ORDER, get_setting("model") or "sonnet")


def clamp_effort(effort: str) -> str:
    return _clamp(effort, "max_effort", EFFORT_ORDER, "medium")
