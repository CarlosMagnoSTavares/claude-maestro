"""Camada de persistência SQLite (stdlib, caminho absoluto, thread-safe)."""
import sqlite3
import threading
import json
import hashlib
import datetime

from . import config

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    objectives TEXT DEFAULT '',
    impediments TEXT DEFAULT '',
    problems TEXT DEFAULT '',
    extra_json TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS projects(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    folder TEXT NOT NULL,
    description TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    mission TEXT NOT NULL,
    mode TEXT DEFAULT 'yolo',
    autonomy INTEGER DEFAULT 1,
    status TEXT DEFAULT 'nova',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agents(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    role TEXT DEFAULT '',
    description TEXT DEFAULT '',
    system_prompt TEXT DEFAULT '',
    skills_json TEXT DEFAULT '[]',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    status TEXT DEFAULT 'preparando',
    phase TEXT DEFAULT '',
    summary TEXT DEFAULT '',
    total_cost_usd REAL DEFAULT 0,
    error TEXT DEFAULT '',
    resume_at TEXT DEFAULT '',
    budget_approved INTEGER DEFAULT 0,
    started_at TEXT,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS run_agents(
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    agent_name TEXT NOT NULL,
    UNIQUE(run_id, agent_name)
);
CREATE TABLE IF NOT EXISTS approvals(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    task_id INTEGER NOT NULL,
    kind TEXT NOT NULL,            -- 'plan' | 'budget'
    payload TEXT DEFAULT '',
    status TEXT DEFAULT 'pendente',-- pendente | aprovada | rejeitada
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS consents(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    scope TEXT NOT NULL,           -- ex.: 'task:12'
    level INTEGER NOT NULL,
    term_version TEXT NOT NULL,
    term_hash TEXT NOT NULL,
    user_host TEXT DEFAULT '',
    details TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS microtasks(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    ord INTEGER DEFAULT 0,
    description TEXT NOT NULL,
    agent_name TEXT DEFAULT '',
    priority INTEGER DEFAULT 1,
    parallel INTEGER DEFAULT 1,
    depends_on TEXT DEFAULT '[]',
    status TEXT DEFAULT 'pendente',
    output TEXT DEFAULT '',
    cost_usd REAL DEFAULT 0,
    started_at TEXT,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS skills_cache(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_repo TEXT NOT NULL,
    skill TEXT NOT NULL,
    installs INTEGER DEFAULT 0,
    description TEXT DEFAULT '',
    fetched_at TEXT NOT NULL,
    UNIQUE(owner_repo, skill)
);
CREATE TABLE IF NOT EXISTS schedules(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    time_hhmm TEXT NOT NULL,
    days TEXT DEFAULT '1111111',
    enabled INTEGER DEFAULT 1,
    last_run_date TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS events(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    task_id INTEGER,
    ts TEXT NOT NULL,
    level TEXT DEFAULT 'info',
    message TEXT NOT NULL,
    prev_hash TEXT DEFAULT '',
    hash TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS notifications(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT DEFAULT '',
    read INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settings(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS wa_operators(
    phone TEXT PRIMARY KEY,        -- só dígitos (ex.: 5511999999999)
    name TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS wa_sessions(
    phone TEXT PRIMARY KEY,
    state TEXT DEFAULT 'menu',
    context TEXT DEFAULT '{}',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_messages(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    ts TEXT NOT NULL,
    from_agent TEXT NOT NULL,
    to_agent TEXT NOT NULL,
    kind TEXT DEFAULT 'mensagem',   -- 'mensagem' | 'backlog'
    content TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS suggestions(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER,
    project_id INTEGER,
    task_id INTEGER,
    run_id INTEGER,
    title TEXT NOT NULL,
    mission TEXT NOT NULL,
    reason TEXT DEFAULT '',
    status TEXT DEFAULT 'pendente',   -- 'pendente' | 'aceita' | 'descartada'
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_calls(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    microtask_id INTEGER,
    ts TEXT NOT NULL,
    kind TEXT DEFAULT '',          -- 'recrutar' | 'planejar' | 'executar'
    agent_name TEXT DEFAULT '',
    model TEXT DEFAULT '',
    effort TEXT DEFAULT '',
    prompt TEXT DEFAULT '',
    system_prompt TEXT DEFAULT '',
    args TEXT DEFAULT '',
    stdout TEXT DEFAULT '',
    stderr TEXT DEFAULT '',
    thinking TEXT DEFAULT '',
    result TEXT DEFAULT '',
    ok INTEGER DEFAULT 0,
    error TEXT DEFAULT '',
    cost_usd REAL DEFAULT 0,
    duration_ms INTEGER DEFAULT 0
);
"""


def now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _dict_factory(cursor, row):
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


# colunas adicionadas após a v1 (migração idempotente p/ bancos existentes)
_MIGRATIONS = [
    "ALTER TABLE tasks ADD COLUMN autonomy INTEGER DEFAULT 1",
    "ALTER TABLE runs ADD COLUMN resume_at TEXT DEFAULT ''",
    "ALTER TABLE runs ADD COLUMN budget_approved INTEGER DEFAULT 0",
    "ALTER TABLE events ADD COLUMN prev_hash TEXT DEFAULT ''",
    "ALTER TABLE events ADD COLUMN hash TEXT DEFAULT ''",
    "ALTER TABLE agents ADD COLUMN company_id INTEGER",
    "ALTER TABLE agents ADD COLUMN model TEXT DEFAULT ''",
    "ALTER TABLE agents ADD COLUMN effort TEXT DEFAULT ''",
    "ALTER TABLE agents ADD COLUMN reports_to TEXT DEFAULT ''",
    "ALTER TABLE runs ADD COLUMN feedback TEXT DEFAULT ''",
    "ALTER TABLE approvals ADD COLUMN reason TEXT DEFAULT ''",
]


def init():
    """Abre conexão, cria schema, aplica migrações e carrega settings no cache."""
    global _conn
    config.ensure_dirs()
    with _lock:
        if _conn is None:
            _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
            _conn.row_factory = _dict_factory
            _conn.execute("PRAGMA foreign_keys = ON")
            _conn.executescript(SCHEMA)
            for mig in _MIGRATIONS:
                try:
                    _conn.execute(mig)
                except sqlite3.OperationalError:
                    pass  # coluna já existe
            _conn.commit()
    rows = {r["key"]: r["value"] for r in query("SELECT key, value FROM settings")}
    config.load_settings_cache(rows)


def query(sql: str, params: tuple = ()) -> list[dict]:
    with _lock:
        cur = _conn.execute(sql, params)
        return cur.fetchall()


def query_one(sql: str, params: tuple = ()) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: tuple = ()) -> int:
    """Executa e retorna lastrowid."""
    with _lock:
        cur = _conn.execute(sql, params)
        _conn.commit()
        return cur.lastrowid


def executemany(sql: str, seq: list[tuple]):
    with _lock:
        _conn.executemany(sql, seq)
        _conn.commit()


# ---------- helpers de domínio ----------

def set_setting(key: str, value: str):
    execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    config.set_setting_cache(key, value)


def add_event(message: str, run_id=None, task_id=None, level="info"):
    """Registra evento na trilha de auditoria encadeada por hash (imutável/verificável)."""
    with _lock:
        prev = query_one("SELECT hash FROM events ORDER BY id DESC LIMIT 1")
        prev_hash = (prev or {}).get("hash") or ""
        ts = now()
        payload = f"{prev_hash}|{ts}|{level}|{run_id}|{task_id}|{message}"
        h = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        _conn.execute(
            "INSERT INTO events(run_id, task_id, ts, level, message, prev_hash, hash) "
            "VALUES(?,?,?,?,?,?,?)",
            (run_id, task_id, ts, level, message, prev_hash, h),
        )
        _conn.commit()


def verify_audit_chain() -> dict:
    """Reverifica a cadeia de hashes da auditoria. Retorna {ok, total, broken_at}."""
    rows = query("SELECT * FROM events ORDER BY id")
    prev_hash = ""
    for r in rows:
        payload = f"{prev_hash}|{r['ts']}|{r['level']}|{r['run_id']}|{r['task_id']}|{r['message']}"
        h = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        if h != r["hash"] or r["prev_hash"] != prev_hash:
            return {"ok": False, "total": len(rows), "broken_at": r["id"]}
        prev_hash = h
    return {"ok": True, "total": len(rows), "broken_at": None}


def add_llm_call(ctx: dict, *, model: str, effort: str, prompt: str,
                 system_prompt: str, args: str, stdout: str, stderr: str,
                 thinking: str, result: str, ok: bool, error: str,
                 cost_usd: float, duration_ms: int) -> int:
    """Registra uma chamada RAW ao Claude (debug/transparência total)."""
    return execute(
        "INSERT INTO llm_calls(run_id, microtask_id, ts, kind, agent_name, model, effort, "
        "prompt, system_prompt, args, stdout, stderr, thinking, result, ok, error, "
        "cost_usd, duration_ms) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (ctx.get("run_id"), ctx.get("microtask_id"), now(), ctx.get("kind", ""),
         ctx.get("agent_name", ""), model or "", effort or "", prompt or "",
         system_prompt or "", args or "", (stdout or "")[:400_000],
         (stderr or "")[:100_000], (thinking or "")[:200_000], (result or "")[:200_000],
         1 if ok else 0, (error or "")[:4000], cost_usd or 0, duration_ms or 0),
    )


def add_notification(title: str, message: str = "") -> int:
    return execute(
        "INSERT INTO notifications(ts, title, message) VALUES(?,?,?)",
        (now(), title, message),
    )


def export_all() -> dict:
    """Dump completo do domínio para o pacote de exportação."""
    return {
        "companies": query("SELECT * FROM companies"),
        "projects": query("SELECT * FROM projects"),
        "tasks": query("SELECT * FROM tasks"),
        "agents": query("SELECT * FROM agents"),
        "schedules": query("SELECT * FROM schedules"),
        "runs": query("SELECT * FROM runs"),
        "microtasks": query("SELECT * FROM microtasks"),
        "settings": query("SELECT * FROM settings"),
        "consents": query("SELECT * FROM consents"),
        "events": query("SELECT * FROM events"),
    }


def seed_company_from_cnpj_json(path: str) -> int | None:
    """Cria empresa a partir de um JSON de CNPJ (Receita) se ainda não existir."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    name = data.get("nome_fantasia") or data.get("razao_social")
    if not name:
        return None
    if query_one("SELECT id FROM companies WHERE name = ?", (name,)):
        return None
    atividade = data.get("cnae_fiscal_descricao", "")
    secundarias = ", ".join(
        c.get("descricao", "") for c in data.get("cnaes_secundarios", [])
    )
    objectives = (
        f"Razão social: {data.get('razao_social', '')}. "
        f"Atividade principal: {atividade}. "
        f"Atividades secundárias: {secundarias}."
    )
    return execute(
        "INSERT INTO companies(name, objectives, impediments, problems, extra_json, created_at) "
        "VALUES(?,?,?,?,?,?)",
        (name, objectives, "", "", json.dumps(data, ensure_ascii=False), now()),
    )
