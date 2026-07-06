"""API REST do Claude Maestro (FastAPI) + frontend estático.

Documentação interativa (Swagger) em /docs — pensada para orquestração por
outros agentes/Maestros. Requisições vindas do túnel externo (X-Forwarded-For)
exigem o token de API (header `X-API-Token` ou `Authorization: Bearer ...`).
"""
import os
import json
import secrets
import sqlite3
import subprocess
import threading

from fastapi import FastAPI, Body, UploadFile, File, HTTPException, Security, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles

from . import (config, db, bootstrap, claude_cli, skills_catalog,
               orchestrator, scheduler, porter, notify, governance, tunnel,
               whatsapp, mission_templates, deliverables)

# aparece no botão Authorize do Swagger (uso remoto)
_api_key_scheme = APIKeyHeader(name="X-API-Token", auto_error=False)

app = FastAPI(
    title=f"{config.APP_NAME} API",
    version=config.APP_VERSION,
    description=(
        "API local do Claude Maestro — tudo que a interface faz, outro agente pode fazer. "
        "Acesso local é livre; pelo túnel externo, envie o token em `X-API-Token`."
    ),
    dependencies=[Security(_api_key_scheme)],
)


@app.on_event("startup")
def _startup():
    db.init()
    db.seed_company_from_cnpj_json(config.SEED_COMPANY_JSON)
    if not config.get_setting("api_token"):  # token de orquestração externa
        db.set_setting("api_token", secrets.token_urlsafe(24))
    orchestrator.recover_orphans()   # runs interrompidos por reinício voltam pelo heartbeat
    scheduler.start_loop()
    whatsapp.autostart()             # religa o WhatsApp se estava conectado
    threading.Thread(target=skills_catalog.refresh_catalog, daemon=True).start()


@app.middleware("http")
async def _external_token_guard(request, call_next):
    """Só exige token quando a requisição atravessou o túnel (X-Forwarded-For)."""
    if request.url.path.startswith("/api") and request.headers.get("x-forwarded-for"):
        token = config.get_setting("api_token")
        sent = request.headers.get("x-api-token") or ""
        auth = request.headers.get("authorization") or ""
        if auth.lower().startswith("bearer "):
            sent = sent or auth[7:].strip()
        if not token or sent != token:
            return JSONResponse(status_code=401,
                                content={"detail": "Token de API inválido ou ausente (X-API-Token)."})
    return await call_next(request)


def _err(msg: str, code: int = 400):
    raise HTTPException(status_code=code, detail=msg)


@app.exception_handler(sqlite3.IntegrityError)
def _integrity_handler(request, exc):
    return JSONResponse(status_code=400,
                        content={"detail": f"Referência inválida: {exc}"})


# ------------------------------------------------------------ status/bootstrap

@app.get("/api/status")
def api_status():
    st = bootstrap.status()
    st["app_version"] = config.APP_VERSION
    st["running"] = [t["id"] for t in db.query("SELECT id FROM tasks WHERE status='executando'")]
    return st


@app.post("/api/bootstrap/install")
def api_install():
    return bootstrap.install_claude()


@app.post("/api/bootstrap/node")
def api_install_node():
    return bootstrap.install_node()


@app.post("/api/bootstrap/bypass")
def api_bypass():
    return bootstrap.enable_bypass()


@app.post("/api/bootstrap/login")
def api_login():
    return bootstrap.open_login_terminal()


# ------------------------------------------------------------ empresas

@app.get("/api/companies")
def list_companies():
    return db.query("SELECT * FROM companies ORDER BY id DESC")


@app.post("/api/companies")
def create_company(payload: dict = Body(...)):
    name = (payload.get("name") or "").strip()
    if not name:
        _err("Informe o nome da empresa.")
    cid = db.execute(
        "INSERT INTO companies(name, objectives, impediments, problems, extra_json, created_at) "
        "VALUES(?,?,?,?,?,?)",
        (name, payload.get("objectives", ""), payload.get("impediments", ""),
         payload.get("problems", ""), "", db.now()),
    )
    return {"id": cid}


@app.put("/api/companies/{cid}")
def update_company(cid: int, payload: dict = Body(...)):
    db.execute(
        "UPDATE companies SET name=?, objectives=?, impediments=?, problems=? WHERE id=?",
        (payload.get("name", ""), payload.get("objectives", ""),
         payload.get("impediments", ""), payload.get("problems", ""), cid),
    )
    return {"ok": True}


@app.delete("/api/companies/{cid}")
def delete_company(cid: int):
    db.execute("DELETE FROM companies WHERE id=?", (cid,))
    return {"ok": True}


# ------------------------------------------------------------ projetos

@app.get("/api/projects")
def list_projects(company_id: int | None = None):
    sql = ("SELECT p.*, c.name AS company_name FROM projects p "
           "JOIN companies c ON c.id = p.company_id ")
    params: tuple = ()
    if company_id:
        sql += "WHERE p.company_id=? "
        params = (company_id,)
    return db.query(sql + "ORDER BY p.id DESC", params)


@app.post("/api/projects")
def create_project(payload: dict = Body(...)):
    name = (payload.get("name") or "").strip()
    folder = (payload.get("folder") or "").strip()
    company_id = payload.get("company_id")
    if not (name and folder and company_id):
        _err("Informe nome, pasta e empresa do projeto.")
    os.makedirs(folder, exist_ok=True)
    pid = db.execute(
        "INSERT INTO projects(company_id, name, folder, description, created_at) VALUES(?,?,?,?,?)",
        (company_id, name, folder, payload.get("description", ""), db.now()),
    )
    project = db.query_one("SELECT * FROM projects WHERE id=?", (pid,))
    company = db.query_one("SELECT * FROM companies WHERE id=?", (company_id,))
    if company:
        bootstrap.write_project_claude_md(project, company)
    return {"id": pid}


@app.put("/api/projects/{pid}")
def update_project(pid: int, payload: dict = Body(...)):
    project = db.query_one("SELECT * FROM projects WHERE id=?", (pid,))
    if not project:
        _err("Projeto não encontrado.", 404)
    name = (payload.get("name") or project["name"]).strip()
    folder = (payload.get("folder") or project["folder"]).strip()
    os.makedirs(folder, exist_ok=True)
    db.execute(
        "UPDATE projects SET name=?, folder=?, description=? WHERE id=?",
        (name, folder, payload.get("description", project.get("description") or ""), pid),
    )
    project = db.query_one("SELECT * FROM projects WHERE id=?", (pid,))
    company = db.query_one("SELECT * FROM companies WHERE id=?", (project["company_id"],))
    if company:
        bootstrap.write_project_claude_md(project, company)
    return {"ok": True}


@app.delete("/api/projects/{pid}")
def delete_project(pid: int):
    db.execute("DELETE FROM projects WHERE id=?", (pid,))
    return {"ok": True}


@app.post("/api/pick-folder")
def pick_folder():
    """Abre o seletor de pastas nativo do Windows (PowerShell: funciona também no app instalado)."""
    script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "Add-Type -AssemblyName System.Drawing; "
        "$owner = New-Object System.Windows.Forms.Form; "
        "$owner.TopMost = $true; "
        "$owner.StartPosition = 'CenterScreen'; "
        "$owner.Size = New-Object System.Drawing.Size(0,0); "
        "$owner.ShowInTaskbar = $false; "
        "$owner.Opacity = 0; "
        "$owner.Show(); "
        "$owner.Activate(); "
        "$f = New-Object System.Windows.Forms.FolderBrowserDialog; "
        "$f.Description = 'Escolha a pasta do projeto'; "
        "$f.ShowNewFolderButton = $true; "
        "if ($f.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) "
        "{ Write-Output $f.SelectedPath }; "
        "$owner.Close()"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", script],
            capture_output=True, text=True, timeout=300,
            encoding="utf-8", errors="replace",
            creationflags=config.CREATE_NO_WINDOW)
        folder = (proc.stdout or "").strip().splitlines()
        return {"folder": (folder[-1].strip() if folder else "")}
    except (OSError, subprocess.TimeoutExpired):
        return {"folder": ""}


# ------------------------------------------------------------ modelos de missão

@app.get("/api/mission-templates")
def list_mission_templates():
    """Modelos prontos de missão (curadoria para pequenas empresas)."""
    return mission_templates.list_templates()


# ------------------------------------------------------------ entregas (deliverables)

@app.get("/api/runs/{run_id}/deliverables")
def run_deliverables(run_id: int):
    """Arquivos produzidos/alterados na pasta do projeto durante esta execução."""
    r = deliverables.for_run(run_id)
    if r is None:
        _err("Execução não encontrada.", 404)
    return r


@app.get("/api/runs/{run_id}/deliverables/file")
def run_deliverable_file(run_id: int, path: str):
    """Baixa/abre um arquivo entregue (path relativo à pasta do projeto)."""
    full = deliverables.safe_path(run_id, path)
    if not full:
        _err("Arquivo não encontrado.", 404)
    return FileResponse(full, filename=os.path.basename(full))


@app.post("/api/tasks/{tid}/open-folder")
def open_task_folder(tid: int):
    """Abre a pasta do projeto no Explorer do Windows."""
    row = db.query_one(
        "SELECT p.folder FROM tasks t JOIN projects p ON p.id=t.project_id WHERE t.id=?", (tid,))
    if not (row and os.path.isdir(row["folder"])):
        _err("Pasta do projeto não encontrada.", 404)
    os.startfile(row["folder"])  # noqa: S606 — ação explícita do gestor local
    return {"ok": True}


# ------------------------------------------------------------ sugestões proativas

@app.get("/api/suggestions")
def list_suggestions(company_id: int | None = None):
    """Missões sugeridas pela equipe após as retrospectivas (pendentes primeiro)."""
    sql = ("SELECT s.*, p.name AS project_name, c.name AS company_name, "
           "t.title AS origin_title FROM suggestions s "
           "LEFT JOIN projects p ON p.id=s.project_id "
           "LEFT JOIN companies c ON c.id=s.company_id "
           "LEFT JOIN tasks t ON t.id=s.task_id ")
    params: tuple = ()
    if company_id:
        sql += "WHERE s.company_id=? "
        params = (company_id,)
    return db.query(sql + "ORDER BY s.status='pendente' DESC, s.id DESC LIMIT 50", params)


@app.post("/api/suggestions/{sid}/accept")
def accept_suggestion(sid: int, payload: dict = Body(default={})):
    """Aceita a sugestão: vira missão de verdade (e opcionalmente já executa)."""
    s = db.query_one("SELECT * FROM suggestions WHERE id=? AND status='pendente'", (sid,))
    if not s:
        _err("Sugestão não encontrada ou já resolvida.", 404)
    tid = db.execute(
        "INSERT INTO tasks(project_id, title, mission, mode, autonomy, status, created_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (s["project_id"], s["title"], s["mission"], "yolo", 1, "nova", db.now()),
    )
    db.execute("UPDATE suggestions SET status='aceita' WHERE id=?", (sid,))
    db.add_event(f"Sugestão da equipe aceita pelo gestor: {s['title']}", task_id=tid)
    run_id = None
    if payload.get("start"):
        try:
            run_id = orchestrator.start_task(tid)
        except ValueError as e:
            _err(str(e))
    return {"task_id": tid, "run_id": run_id}


@app.post("/api/suggestions/{sid}/dismiss")
def dismiss_suggestion(sid: int):
    db.execute("UPDATE suggestions SET status='descartada' WHERE id=? AND status='pendente'",
               (sid,))
    return {"ok": True}


# ------------------------------------------------------------ missões (tasks)

@app.get("/api/tasks")
def list_tasks(company_id: int | None = None):
    sql = (
        "SELECT t.*, p.name AS project_name, c.name AS company_name, c.id AS company_id, "
        "(SELECT id FROM runs WHERE task_id=t.id ORDER BY id DESC LIMIT 1) AS last_run_id, "
        "(SELECT total_cost_usd FROM runs WHERE task_id=t.id ORDER BY id DESC LIMIT 1) AS last_cost "
        "FROM tasks t JOIN projects p ON p.id=t.project_id "
        "JOIN companies c ON c.id=p.company_id "
    )
    params: tuple = ()
    if company_id:
        sql += "WHERE c.id=? "
        params = (company_id,)
    return db.query(sql + "ORDER BY t.id DESC", params)


@app.post("/api/tasks")
def create_task(payload: dict = Body(...)):
    title = (payload.get("title") or "").strip()
    mission = (payload.get("mission") or "").strip()
    project_id = payload.get("project_id")
    if not (title and mission and project_id):
        _err("Informe título, missão e projeto.")
    try:
        autonomy = int(payload.get("autonomy", config.get_setting_int("autonomy_default")))
    except (TypeError, ValueError):
        autonomy = 1
    if autonomy not in governance.AUTONOMY_LEVELS:
        autonomy = 1
    # nível autônomo exige aceite do Termo de Risco (consentimento registrado)
    if autonomy >= 2 and not payload.get("accept_risk"):
        _err("Para o modo autônomo é obrigatório aceitar o Termo de Ciência e Assunção de Risco.")
    tid = db.execute(
        "INSERT INTO tasks(project_id, title, mission, mode, autonomy, status, created_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (project_id, title, mission, payload.get("mode", "yolo"), autonomy, "nova", db.now()),
    )
    if autonomy >= 2:
        governance.record_consent(f"task:{tid}", autonomy, details=title)
    return {"id": tid}


@app.delete("/api/tasks/{tid}")
def delete_task(tid: int):
    if orchestrator.is_task_running(tid):
        _err("Missão em execução; cancele antes de excluir.")
    db.execute("DELETE FROM tasks WHERE id=?", (tid,))
    return {"ok": True}


@app.post("/api/tasks/{tid}/start")
def start_task(tid: int):
    st = bootstrap.status()
    if not st["claude_installed"]:
        _err("Claude Code não está instalado. Vá em Instalação.")
    try:
        run_id = orchestrator.start_task(tid)
    except ValueError as e:
        _err(str(e))
    return {"run_id": run_id}


@app.get("/api/tasks/{tid}")
def task_detail(tid: int):
    task = db.query_one(
        "SELECT t.*, p.name AS project_name, p.folder, c.name AS company_name, "
        "c.id AS company_id FROM tasks t JOIN projects p ON p.id=t.project_id "
        "JOIN companies c ON c.id=p.company_id WHERE t.id=?", (tid,))
    if not task:
        _err("Missão não encontrada.", 404)
    runs = db.query("SELECT * FROM runs WHERE task_id=? ORDER BY id DESC", (tid,))
    latest = runs[0] if runs else None
    microtasks, events, approvals, agents, messages = [], [], [], [], []
    if latest:
        microtasks = db.query("SELECT * FROM microtasks WHERE run_id=? ORDER BY ord", (latest["id"],))
        events = db.query("SELECT * FROM events WHERE run_id=? ORDER BY id DESC LIMIT 100", (latest["id"],))
        approvals = db.query("SELECT * FROM approvals WHERE run_id=? ORDER BY id DESC", (latest["id"],))
        agents = db.query(
            "SELECT a.id, a.name, a.role, a.model, a.effort, a.reports_to FROM run_agents ra "
            "JOIN agents a ON a.name = ra.agent_name WHERE ra.run_id=? ORDER BY a.name",
            (latest["id"],))
        messages = db.query("SELECT * FROM agent_messages WHERE run_id=? ORDER BY id",
                            (latest["id"],))
    return {"task": task, "runs": runs, "latest_run": latest, "agents": agents,
            "microtasks": microtasks, "events": events, "approvals": approvals,
            "messages": messages}


@app.post("/api/runs/{run_id}/cancel")
def cancel_run(run_id: int):
    orchestrator.cancel_run(run_id)
    return {"ok": True}


@app.post("/api/runs/{run_id}/resume")
def resume_run(run_id: int):
    ok = orchestrator.resume_run(run_id, reason="retomada manual pelo gestor.")
    if not ok:
        _err("Este run não está pausado (ou já está rodando).")
    return {"ok": True}


# ------------------------------------------------------------ governança

@app.get("/api/governance/term")
def governance_term():
    return {"version": governance.TERM_VERSION, "hash": governance.term_hash(),
            "text": governance.RISK_TERM, "levels": governance.AUTONOMY_LEVELS}


@app.get("/api/approvals")
def list_approvals(company_id: int | None = None):
    sql = (
        "SELECT ap.*, t.title AS task_title, c.name AS company_name, c.id AS company_id "
        "FROM approvals ap JOIN tasks t ON t.id = ap.task_id "
        "JOIN projects p ON p.id = t.project_id JOIN companies c ON c.id = p.company_id "
    )
    params: tuple = ()
    if company_id:
        sql += "WHERE c.id=? "
        params = (company_id,)
    return db.query(sql + "ORDER BY ap.status='pendente' DESC, ap.id DESC LIMIT 100", params)


@app.post("/api/approvals/{aid}/resolve")
def resolve_approval(aid: int, payload: dict = Body(...)):
    """Aprova, reprova (com motivo obrigatório → replaneja) ou cancela a missão inteira.

    payload: {approve: bool, reason?: str, cancel?: bool, agents_config?: [...]}
    - approve=true  → segue a execução;
    - approve=false + cancel=true → cancela o run inteiro;
    - approve=false (plan) → reason obrigatório; o Maestro replaneja usando o motivo.
    """
    approve = bool(payload.get("approve"))
    cancel = bool(payload.get("cancel"))
    reason = str(payload.get("reason") or "").strip()
    if not approve and not cancel and not reason:
        _err("Informe o motivo da reprovação — a IA usa esse feedback para melhorar o plano.")
    if approve:
        # gestor pode rebaixar modelo/esforço de cada agente ao aprovar o plano
        for cfg in payload.get("agents_config") or []:
            name = str(cfg.get("name") or "").strip()
            if not name:
                continue
            db.execute(
                "UPDATE agents SET model=?, effort=? WHERE name=?",
                (config.clamp_model(str(cfg.get("model") or "")),
                 config.clamp_effort(str(cfg.get("effort") or "")), name),
            )
    a = orchestrator.apply_approval_decision(aid, approve, reason=reason, cancel=cancel)
    if not a:
        _err("Aprovação não encontrada ou já resolvida.", 404)
    if approve and payload.get("agents_config"):
        db.add_event("Gestor ajustou modelo/esforço da equipe na aprovação.",
                     run_id=a["run_id"], task_id=a["task_id"])
    return {"ok": True}


@app.get("/api/consents")
def list_consents():
    return db.query("SELECT * FROM consents ORDER BY id DESC LIMIT 100")


@app.get("/api/audit/export")
def audit_export():
    check = db.verify_audit_chain()
    payload = {
        "gerado_em": db.now(),
        "verificacao": check,
        "termo": {"version": governance.TERM_VERSION, "hash": governance.term_hash()},
        "consentimentos": db.query("SELECT * FROM consents ORDER BY id"),
        "eventos": db.query("SELECT * FROM events ORDER BY id"),
    }
    config.ensure_dirs()
    path = os.path.join(config.EXPORTS_DIR, "auditoria_maestro.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return FileResponse(path, filename="auditoria_maestro.json",
                        media_type="application/json")


@app.get("/api/audit/verify")
def audit_verify():
    return db.verify_audit_chain()


# ------------------------------------------------------------ acesso externo (túnel)

@app.get("/api/tunnel")
def tunnel_status():
    return tunnel.status()


@app.post("/api/tunnel/start")
def tunnel_start(request: Request):
    try:
        port = request.scope.get("server", (None, config.DEFAULT_PORT))[1] or config.DEFAULT_PORT
    except Exception:
        port = config.DEFAULT_PORT
    st = tunnel.start(port)
    if not st["running"] and st.get("error"):
        _err(st["error"])
    return st


@app.post("/api/tunnel/stop")
def tunnel_stop():
    return tunnel.stop()


@app.post("/api/token/rotate")
def rotate_token():
    """Gera um novo token de API (invalida o anterior)."""
    new = secrets.token_urlsafe(24)
    db.set_setting("api_token", new)
    db.add_event("Token de API rotacionado.", level="warn")
    return {"api_token": new}


# ------------------------------------------------------------ jornada guiada

@app.get("/api/journey")
def journey():
    """Estado da jornada de primeiro uso (guia o usuário passo a passo)."""
    st = bootstrap.status()
    has_company = bool(db.query_one("SELECT id FROM companies LIMIT 1"))
    has_project = bool(db.query_one("SELECT id FROM projects LIMIT 1"))
    has_task = bool(db.query_one("SELECT id FROM tasks LIMIT 1"))
    has_run = bool(db.query_one("SELECT id FROM runs LIMIT 1"))
    if not st["ready"]:
        step = 1          # preparar o sistema
    elif not has_company:
        step = 2          # empresa
    elif not has_project:
        step = 3          # projeto
    elif not has_task:
        step = 4          # primeira missão
    else:
        step = 0          # jornada concluída
    return {
        "step": step, "complete": step == 0,
        "system_ready": st["ready"], "has_company": has_company,
        "has_project": has_project, "has_task": has_task, "has_run": has_run,
    }


# ------------------------------------------------------------ dashboard

@app.get("/api/dashboard")
def dashboard(company_id: int | None = None):
    """Números do painel; com `company_id`, restringe ao workspace da empresa."""
    where_task = ""
    params: tuple = ()
    if company_id:
        where_task = ("WHERE task_id IN (SELECT t.id FROM tasks t "
                      "JOIN projects p ON p.id=t.project_id WHERE p.company_id=?)")
        params = (company_id,)
    q1 = db.query_one(
        "SELECT COUNT(*) n FROM tasks t JOIN projects p ON p.id=t.project_id"
        + (" WHERE p.company_id=?" if company_id else ""), params)["n"]
    running = db.query_one(
        f"SELECT COUNT(*) n FROM runs WHERE status IN ('executando','preparando')"
        + (f" AND id IN (SELECT id FROM runs {where_task})" if company_id else ""),
        params)["n"]
    waiting_approval = db.query_one(
        "SELECT COUNT(*) n FROM approvals WHERE status='pendente'"
        + (f" AND run_id IN (SELECT id FROM runs {where_task})" if company_id else ""),
        params)["n"]
    waiting_limit = db.query(
        "SELECT * FROM runs WHERE status='aguardando_limite'"
        + (f" AND id IN (SELECT id FROM runs {where_task})" if company_id else ""),
        params)
    cost = db.query_one(
        "SELECT COALESCE(SUM(total_cost_usd),0) c FROM runs"
        + (f" {where_task}" if company_id else ""), params)["c"]
    agents = db.query_one(
        "SELECT COUNT(*) n FROM agents"
        + (" WHERE company_id IS NULL OR company_id=?" if company_id else ""),
        params)["n"]
    return {
        "tasks": q1, "running": running, "waiting_approval": waiting_approval,
        "waiting_limit": [{"run_id": r["id"], "task_id": r["task_id"],
                           "resume_at": r["resume_at"]} for r in waiting_limit],
        "total_cost_usd": round(cost, 2), "agents": agents,
        "paused": orchestrator.paused_count(company_id),
    }


# ------------------------------------------------------------ whatsapp

@app.get("/api/whatsapp/status")
def wa_status():
    return whatsapp.status()


@app.get("/api/whatsapp/qr.png")
def wa_qr():
    """QR code atual como PNG (para escanear no WhatsApp > Aparelhos conectados)."""
    st = whatsapp.status()
    if not st.get("qr"):
        _err("Sem QR disponível no momento.", 404)
    import io
    import qrcode
    from fastapi.responses import Response
    # correção L: o payload do WhatsApp (Baileys 7) é longo → QR menos denso e mais fácil de ler
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=8, border=2)
    qr.add_data(st["qr"])
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@app.post("/api/whatsapp/start")
def wa_start():
    r = whatsapp.start()
    if not r.get("ok"):
        _err(r.get("error") or "Falha ao iniciar o WhatsApp.")
    return r


@app.post("/api/whatsapp/stop")
def wa_stop(payload: dict = Body(default={})):
    whatsapp.stop(logout=bool(payload.get("logout")))
    return {"ok": True}


@app.post("/api/whatsapp/secret/rotate")
def wa_rotate_secret():
    return {"secret": whatsapp.rotate_secret()}


@app.delete("/api/whatsapp/operators/{phone}")
def wa_del_operator(phone: str):
    db.execute("DELETE FROM wa_operators WHERE phone=?", (phone,))
    db.execute("DELETE FROM wa_sessions WHERE phone=?", (phone,))
    db.add_event(f"Operador WhatsApp revogado: {phone}", level="warn")
    return {"ok": True}


# ------------------------------------------------------------ pausa da empresa

@app.post("/api/company/pause")
def company_pause(company_id: int | None = None):
    """Botão único "Pausar empresa": pausa todas as execuções ativas (memória salva)."""
    n = orchestrator.pause_company(company_id)
    return {"ok": True, "pausing": n}


@app.post("/api/company/resume")
def company_resume(company_id: int | None = None):
    """Retoma todas as execuções pausadas pelo gestor."""
    n = orchestrator.resume_company(company_id)
    return {"ok": True, "resumed": n}


# ------------------------------------------------------------ organograma / equipe ao vivo

@app.get("/api/team/org")
def team_org(company_id: int | None = None):
    """Organograma da equipe com estado ao vivo (quem está trabalhando em quê)."""
    sql = "SELECT a.*, c.name AS company_name FROM agents a LEFT JOIN companies c ON c.id=a.company_id "
    params: tuple = ()
    if company_id:
        sql += "WHERE a.company_id IS NULL OR a.company_id=? "
        params = (company_id,)
    agents = db.query(sql + "ORDER BY a.name", params)
    # micro-tarefa em execução agora, por agente (runs ativos)
    live = db.query(
        "SELECT m.agent_name, m.ord, m.description, m.status, m.run_id, r.task_id, "
        "t.title AS task_title, r.status AS run_status "
        "FROM microtasks m JOIN runs r ON r.id=m.run_id JOIN tasks t ON t.id=r.task_id "
        f"WHERE r.status IN ({','.join('?'*len(orchestrator.ACTIVE))}) "
        "AND m.status IN ('executando','pendente') ORDER BY m.status='executando' DESC, m.ord",
        tuple(orchestrator.ACTIVE),
    )
    by_agent: dict = {}
    for row in live:
        cur = by_agent.setdefault(row["agent_name"], {"working": None, "queue": 0})
        if row["status"] == "executando" and not cur["working"]:
            cur["working"] = {"ord": row["ord"], "description": row["description"][:160],
                              "task_id": row["task_id"], "task_title": row["task_title"],
                              "run_status": row["run_status"]}
        elif row["status"] == "pendente":
            cur["queue"] += 1
    msgs = db.query(
        "SELECT am.* FROM agent_messages am JOIN runs r ON r.id=am.run_id "
        f"WHERE r.status IN ({','.join('?'*len(orchestrator.ACTIVE))}) "
        "ORDER BY am.id DESC LIMIT 20", tuple(orchestrator.ACTIVE))
    for a in agents:
        a["skills"] = json.loads(a.get("skills_json") or "[]")
        a.pop("system_prompt", None)
        st = by_agent.get(a["name"], {})
        a["working"] = st.get("working")
        a["queue"] = st.get("queue", 0)
    return {"agents": agents, "messages": msgs,
            "paused": orchestrator.paused_count(company_id)}


@app.get("/api/runs/{run_id}/messages")
def run_messages(run_id: int):
    """Conversa da equipe (mensagens e backlog) durante o run."""
    return db.query("SELECT * FROM agent_messages WHERE run_id=? ORDER BY id", (run_id,))


# ------------------------------------------------------------ agentes & skills

@app.get("/api/agents")
def list_agents(company_id: int | None = None):
    sql = "SELECT a.*, c.name AS company_name FROM agents a LEFT JOIN companies c ON c.id=a.company_id "
    params: tuple = ()
    if company_id:
        sql += "WHERE a.company_id IS NULL OR a.company_id=? "
        params = (company_id,)
    rows = db.query(sql + "ORDER BY a.name", params)
    for r in rows:
        r["skills"] = json.loads(r.get("skills_json") or "[]")
    return rows


@app.get("/api/agents/{aid}")
def agent_detail(aid: int):
    """Ficha completa do agente: prompt, skills, modelo/esforço e missões em que atuou."""
    a = db.query_one(
        "SELECT a.*, c.name AS company_name FROM agents a "
        "LEFT JOIN companies c ON c.id=a.company_id WHERE a.id=?", (aid,))
    if not a:
        _err("Agente não encontrado.", 404)
    a["skills"] = json.loads(a.get("skills_json") or "[]")
    a["missions"] = db.query(
        "SELECT DISTINCT t.id AS task_id, t.title, r.status, r.started_at "
        "FROM run_agents ra JOIN runs r ON r.id=ra.run_id "
        "JOIN tasks t ON t.id=r.task_id WHERE ra.agent_name=? "
        "ORDER BY r.id DESC LIMIT 20", (a["name"],))
    return a


@app.delete("/api/agents/{aid}")
def delete_agent(aid: int):
    db.execute("DELETE FROM agents WHERE id=?", (aid,))
    return {"ok": True}


@app.get("/api/skills")
def list_skills():
    return skills_catalog.top_skills(50)


@app.post("/api/skills/refresh")
def refresh_skills():
    return skills_catalog.refresh_catalog(force=True)


# ------------------------------------------------------------ agenda

@app.get("/api/schedules")
def list_schedules(company_id: int | None = None):
    sql = ("SELECT s.*, t.title AS task_title FROM schedules s "
           "JOIN tasks t ON t.id=s.task_id "
           "JOIN projects p ON p.id=t.project_id ")
    params: tuple = ()
    if company_id:
        sql += "WHERE p.company_id=? "
        params = (company_id,)
    return db.query(sql + "ORDER BY s.id DESC", params)


@app.post("/api/schedules")
def create_schedule(payload: dict = Body(...)):
    task_id = payload.get("task_id")
    time_hhmm = (payload.get("time_hhmm") or "").strip()
    if not (task_id and len(time_hhmm) == 5 and time_hhmm[2] == ":"):
        _err("Informe a missão e o horário (HH:MM).")
    sid = db.execute(
        "INSERT INTO schedules(task_id, time_hhmm, days, enabled) VALUES(?,?,?,1)",
        (task_id, time_hhmm, payload.get("days", "1111111")),
    )
    return {"id": sid}


@app.delete("/api/schedules/{sid}")
def delete_schedule(sid: int):
    db.execute("DELETE FROM schedules WHERE id=?", (sid,))
    return {"ok": True}


@app.post("/api/schedules/{sid}/toggle")
def toggle_schedule(sid: int):
    s = db.query_one("SELECT enabled FROM schedules WHERE id=?", (sid,))
    if not s:
        _err("Agendamento não encontrado.", 404)
    db.execute("UPDATE schedules SET enabled=? WHERE id=?", (0 if s["enabled"] else 1, sid))
    return {"ok": True}


@app.post("/api/schedules/{sid}/windows")
def schedule_windows(sid: int):
    return scheduler.register_windows_task(sid)


# ------------------------------------------------------------ notificações & eventos

@app.get("/api/notifications")
def list_notifications():
    return db.query("SELECT * FROM notifications ORDER BY id DESC LIMIT 50")


@app.post("/api/notifications/read")
def mark_notifications_read():
    db.execute("UPDATE notifications SET read=1 WHERE read=0")
    return {"ok": True}


@app.get("/api/events")
def list_events():
    return db.query("SELECT * FROM events ORDER BY id DESC LIMIT 60")


# ------------------------------------------------------------ debug (RAW)

@app.get("/api/runs/{run_id}/llm-calls")
def list_llm_calls(run_id: int):
    """Lista leve das chamadas ao Claude deste run (para o painel de Debug)."""
    return db.query(
        "SELECT id, run_id, microtask_id, ts, kind, agent_name, model, effort, ok, "
        "cost_usd, duration_ms, substr(error, 1, 200) AS error, "
        "length(prompt) AS prompt_len, length(thinking) AS thinking_len, "
        "length(stdout) AS stdout_len "
        "FROM llm_calls WHERE run_id=? ORDER BY id", (run_id,))


@app.get("/api/llm-calls/{cid}")
def llm_call_detail(cid: int):
    """Chamada completa: prompt RAW, system prompt, thinking, resultado e saída bruta."""
    row = db.query_one("SELECT * FROM llm_calls WHERE id=?", (cid,))
    if not row:
        _err("Chamada não encontrada.", 404)
    return row


# ------------------------------------------------------------ ajustes

@app.get("/api/settings")
def get_settings():
    out = dict(config.DEFAULT_SETTINGS)
    for r in db.query("SELECT key, value FROM settings"):
        out[r["key"]] = r["value"]
    return out


@app.post("/api/settings")
def save_settings(payload: dict = Body(...)):
    for k, v in payload.items():
        if k in config.DEFAULT_SETTINGS:
            db.set_setting(k, str(v))
    return {"ok": True}


# ------------------------------------------------------------ exportar/importar

@app.post("/api/export")
def do_export(payload: dict = Body(default={})):
    include = bool(payload.get("include_artifacts", True))
    path = porter.export_package(include_artifacts=include)
    return {"file": os.path.basename(path), "path": path,
            "size_mb": round(os.path.getsize(path) / 1e6, 2)}


@app.get("/api/export/download/{filename}")
def download_export(filename: str):
    safe = os.path.basename(filename)
    path = os.path.join(config.EXPORTS_DIR, safe)
    if not os.path.isfile(path):
        _err("Arquivo não encontrado.", 404)
    return FileResponse(path, filename=safe, media_type="application/zip")


@app.post("/api/import")
async def do_import(file: UploadFile = File(...)):
    config.ensure_dirs()
    dest = os.path.join(config.IMPORTS_DIR, os.path.basename(file.filename or "import.zip"))
    with open(dest, "wb") as f:
        f.write(await file.read())
    try:
        counts = porter.import_package(dest)
    except (ValueError, json.JSONDecodeError, OSError) as e:
        _err(f"Importação falhou: {e}")
    notify.notify("📦 Importação concluída",
                  ", ".join(f"{v} {k}" for k, v in counts.items() if v))
    return counts


# ------------------------------------------------------------ estático (por último)

app.mount("/", StaticFiles(directory=config.STATIC_DIR, html=True), name="static")
