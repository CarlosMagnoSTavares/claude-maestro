"""Orquestrador v2 — máquina de estados resumível por fase (inspirado no core do Paperclip).

Fases de um run: contratando → habilidades → planejando → executando → concluída.
Pausas possíveis (retomáveis pelo heartbeat/aprovação, inclusive após reiniciar o app):
- aguardando_limite    → limite de uso do Claude atingido; retoma no reset (heartbeat);
- aguardando_aprovacao → nível 1 (plano) ou budget hard-stop; gestor decide na UI.
"""
import os
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import db, config, claude_cli, maestro_brain, skills_catalog, bootstrap, notify, governance

_running: dict[int, threading.Thread] = {}   # run_id -> thread
_cancel: set[int] = set()
_pause: set[int] = set()                     # pausa manual do gestor ("pausar empresa")
_state_lock = threading.Lock()

WAITING = ("aguardando_limite", "aguardando_aprovacao", "pausada")
ACTIVE = ("preparando", "executando") + WAITING
MAX_MICROTASKS_PER_RUN = 25   # teto anti-loop para backlog gerado pelos agentes


class _Paused(Exception):
    """Sinaliza que o run foi pausado (limite/aprovação); worker encerra sem falhar."""


def is_run_active_thread(run_id: int) -> bool:
    with _state_lock:
        t = _running.get(run_id)
        return bool(t and t.is_alive())


def is_task_running(task_id: int) -> bool:
    """True se a missão tem run em aberto (executando ou pausado aguardando algo)."""
    rows = db.query(
        f"SELECT id FROM runs WHERE task_id=? AND status IN ({','.join('?'*len(ACTIVE))})",
        (task_id, *ACTIVE),
    )
    return bool(rows)


def start_task(task_id: int) -> int:
    task = db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
    if not task:
        raise ValueError("Missão não encontrada.")
    if is_task_running(task_id):
        raise ValueError("Esta missão já tem uma execução em aberto (veja Aprovações/painel).")
    run_id = db.execute(
        "INSERT INTO runs(task_id, status, phase, started_at) VALUES(?,?,?,?)",
        (task_id, "executando", "contratando", db.now()),
    )
    db.execute("UPDATE tasks SET status='executando' WHERE id=?", (task_id,))
    _spawn(run_id)
    return run_id


def resume_run(run_id: int, reason: str = "") -> bool:
    """Retoma um run pausado (heartbeat, aprovação ou reinício do app)."""
    run = db.query_one("SELECT * FROM runs WHERE id=?", (run_id,))
    if not run or run["status"] not in WAITING:
        return False
    with _state_lock:
        old = _running.get(run_id)
    if old and old.is_alive():
        old.join(timeout=5)  # worker anterior está encerrando (acabou de pausar)
        if old.is_alive():
            return False
    db.execute("UPDATE runs SET status='executando', resume_at='' WHERE id=?", (run_id,))
    db.execute("UPDATE tasks SET status='executando' WHERE id=?", (run["task_id"],))
    if reason:
        db.add_event(f"Execução retomada: {reason}", run_id=run_id, task_id=run["task_id"])
    _spawn(run_id)
    return True


def recover_orphans():
    """No boot: runs que ficaram 'executando' quando o app fechou voltam pela fila do heartbeat."""
    rows = db.query("SELECT * FROM runs WHERE status IN ('executando','preparando')")
    for r in rows:
        db.execute(
            "UPDATE runs SET status='aguardando_limite', resume_at=? WHERE id=?",
            (db.now(), r["id"]),
        )
        db.add_event("Execução interrompida por reinício do app; reagendada pelo heartbeat.",
                     run_id=r["id"], task_id=r["task_id"], level="warn")


def heartbeat_tick():
    """Chamado pelo scheduler (30s): retoma runs cujo horário de reset chegou."""
    now = db.now()
    rows = db.query(
        "SELECT id FROM runs WHERE status='aguardando_limite' AND resume_at!='' AND resume_at<=?",
        (now,),
    )
    for r in rows:
        resume_run(r["id"], reason="heartbeat — janela de limite liberada.")


def cancel_run(run_id: int):
    run = db.query_one("SELECT * FROM runs WHERE id=?", (run_id,))
    if not run:
        return
    if run["status"] in WAITING and not is_run_active_thread(run_id):
        _finish(run_id, run["task_id"], "cancelada", "Cancelada pelo usuário.",
                toast=False)
        return
    with _state_lock:
        _cancel.add(run_id)
    db.add_event("Cancelamento solicitado (efetiva entre micro-tarefas).",
                 run_id=run_id, level="warn")


def apply_approval_decision(approval_id: int, approve: bool,
                            reason: str = "", cancel: bool = False) -> dict | None:
    """Aplica uma decisão de aprovação (usada pela UI e pelo WhatsApp).

    - approve: retoma (budget marca budget_approved);
    - reprova de plano com motivo: replaneja com o feedback;
    - cancel (ou reprova de budget): cancela o run.
    Retorna a approval resolvida ou None se não estava pendente.
    """
    a = governance.resolve_approval(approval_id, approve, reason=reason)
    if not a:
        return None
    if approve:
        if a["kind"] == "budget":
            db.execute("UPDATE runs SET budget_approved=1 WHERE id=?", (a["run_id"],))
        resume_run(a["run_id"], reason=f"aprovação de {a['kind']} concedida.")
    elif cancel or a["kind"] != "plan":
        run = db.query_one("SELECT * FROM runs WHERE id=?", (a["run_id"],))
        if run and run["status"] in WAITING:
            cancel_run(a["run_id"])
    else:
        # plano reprovado com motivo → replaneja com o feedback do gestor
        db.execute("DELETE FROM microtasks WHERE run_id=?", (a["run_id"],))
        db.execute("UPDATE runs SET feedback=?, phase='planejando' WHERE id=?",
                   (reason, a["run_id"]))
        resume_run(a["run_id"],
                   reason="plano reprovado — replanejando com o feedback do gestor.")
    return a


def _cancelled(run_id: int) -> bool:
    with _state_lock:
        return run_id in _cancel


def _pause_requested(run_id: int) -> bool:
    with _state_lock:
        return run_id in _pause


def pause_company(company_id: int | None = None) -> int:
    """Botão "Pausar empresa": pausa todos os runs ativos (efetiva entre micro-tarefas).

    Runs que estão só esperando (aprovação/limite) ficam como estão — já não gastam nada.
    """
    sql = ("SELECT r.id FROM runs r JOIN tasks t ON t.id=r.task_id "
           "JOIN projects p ON p.id=t.project_id "
           "WHERE r.status IN ('executando','preparando')")
    params: tuple = ()
    if company_id:
        sql += " AND p.company_id=?"
        params = (company_id,)
    rows = db.query(sql, params)
    for r in rows:
        with _state_lock:
            _pause.add(r["id"])
    if rows:
        db.add_event(f"Empresa pausada pelo gestor: {len(rows)} execução(ões) serão "
                     "pausadas ao fim da micro-tarefa atual.", level="warn")
    return len(rows)


def resume_company(company_id: int | None = None) -> int:
    """Retoma todos os runs pausados pelo gestor."""
    sql = ("SELECT r.id FROM runs r JOIN tasks t ON t.id=r.task_id "
           "JOIN projects p ON p.id=t.project_id WHERE r.status='pausada'")
    params: tuple = ()
    if company_id:
        sql += " AND p.company_id=?"
        params = (company_id,)
    rows = db.query(sql, params)
    n = 0
    for r in rows:
        if resume_run(r["id"], reason="empresa retomada pelo gestor."):
            n += 1
    return n


def paused_count(company_id: int | None = None) -> int:
    sql = ("SELECT COUNT(*) n FROM runs r JOIN tasks t ON t.id=r.task_id "
           "JOIN projects p ON p.id=t.project_id WHERE r.status='pausada'")
    params: tuple = ()
    if company_id:
        sql += " AND p.company_id=?"
        params = (company_id,)
    return db.query_one(sql, params)["n"]


def _pause_manual(run_id: int, task_id: int, folder: str):
    """Pausa manual: grava MEMORY.md no projeto para o agente retomar sem quebrar."""
    _write_memory(run_id, task_id, folder)
    db.execute("UPDATE runs SET status='pausada', resume_at='' WHERE id=?", (run_id,))
    db.execute("UPDATE tasks SET status='aguardando' WHERE id=?", (task_id,))
    db.add_event("Execução pausada pelo gestor (memória salva em MEMORY.md). "
                 "Retome quando quiser.", run_id=run_id, task_id=task_id, level="warn")


def _write_memory(run_id: int, task_id: int, folder: str):
    """Snapshot do estado do run em MEMORY.md (contexto de retomada dos agentes)."""
    try:
        task = db.query_one("SELECT * FROM tasks WHERE id=?", (task_id,))
        mts = db.query("SELECT * FROM microtasks WHERE run_id=? ORDER BY ord", (run_id,))
        lines = [
            "# MEMORY.md — estado da missão (gerado pelo Claude Maestro ao pausar)",
            f"Atualizado em: {db.now()}",
            f"\n## Missão\n{(task or {}).get('mission', '')}",
            "\n## Micro-tarefas",
        ]
        for m in mts:
            mark = {"concluida": "x"}.get(m["status"], " ")
            lines.append(f"- [{mark}] #{m['ord']} ({m['agent_name']}, {m['status']}): "
                         f"{m['description'][:300]}")
            if m["output"] and m["status"] == "concluida":
                lines.append(f"  - Resultado: {m['output'][:400]}")
        with open(os.path.join(folder, "MEMORY.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        pass  # pasta indisponível não pode impedir a pausa


def _spawn(run_id: int):
    th = threading.Thread(target=_worker, args=(run_id,), daemon=True)
    with _state_lock:
        _running[run_id] = th
    th.start()


# ------------------------------------------------------------------ pipeline

def _worker(run_id: int):
    run = db.query_one("SELECT * FROM runs WHERE id=?", (run_id,))
    task_id = run["task_id"]
    try:
        _advance(run_id, task_id)
    except _Paused:
        pass
    except claude_cli.LimitReached as e:
        _pause_limit(run_id, task_id, e.resume_at)
    except Exception as e:
        db.add_event(f"Erro fatal: {e}", run_id, task_id, level="error")
        _finish(run_id, task_id, "falhou", "", error=str(e)[:1000])
    finally:
        with _state_lock:
            _running.pop(run_id, None)
            _cancel.discard(run_id)
            _pause.discard(run_id)


def _advance(run_id: int, task_id: int):
    task = db.query_one("SELECT * FROM tasks WHERE id=?", (task_id,))
    project = db.query_one("SELECT * FROM projects WHERE id=?", (task["project_id"],))
    company = db.query_one("SELECT * FROM companies WHERE id=?", (project["company_id"],)) if project else None
    if not project or not company:
        raise RuntimeError("Projeto ou empresa não encontrados.")
    bootstrap.write_project_claude_md(project, company)

    run = db.query_one("SELECT * FROM runs WHERE id=?", (run_id,))
    phase = run["phase"] or "contratando"

    if phase == "contratando":
        _phase_recruit(run_id, task_id, task, project, company)
        phase = "habilidades"

    if phase == "habilidades":
        _phase_skills(run_id, task_id, project)
        phase = "planejando"

    if _cancelled(run_id):
        _finish(run_id, task_id, "cancelada", "Cancelada pelo usuário.")
        return

    if phase == "planejando":
        proceed = _phase_plan(run_id, task_id, task, project, company)
        if not proceed:
            return  # pausado (aprovação) ou finalizado (nível 0 / rejeição)
        phase = "executando"

    if phase == "executando":
        _phase_execute(run_id, task_id, task, project)


def _set_phase(run_id: int, phase: str):
    db.execute("UPDATE runs SET phase=? WHERE id=?", (phase, run_id))


def _add_cost(run_id: int, cost: float):
    if cost:
        db.execute("UPDATE runs SET total_cost_usd = total_cost_usd + ? WHERE id=?",
                   (cost, run_id))


def _pause_limit(run_id: int, task_id: int, resume_at: str):
    db.execute("UPDATE runs SET status='aguardando_limite', resume_at=? WHERE id=?",
               (resume_at, run_id))
    db.execute("UPDATE tasks SET status='aguardando' WHERE id=?", (task_id,))
    hhmm = resume_at[11:16] if len(resume_at) >= 16 else resume_at
    db.add_event(f"Limite de uso do Claude atingido. Heartbeat retomará às {hhmm}.",
                 run_id=run_id, task_id=task_id, level="warn")
    notify.notify("Limite do Claude atingido",
                  f"A missão continua sozinha às {hhmm} (heartbeat ativo).")


def _pause_approval(run_id: int, task_id: int):
    db.execute("UPDATE runs SET status='aguardando_aprovacao' WHERE id=?", (run_id,))
    db.execute("UPDATE tasks SET status='aguardando' WHERE id=?", (task_id,))
    notify.notify("Decisão necessária",
                  "Uma missão aguarda sua aprovação no painel do Maestro.")


def _finish(run_id: int, task_id: int, status: str, summary: str,
            error: str = "", toast: bool = True):
    run = db.query_one("SELECT total_cost_usd FROM runs WHERE id=?", (run_id,))
    cost = (run or {}).get("total_cost_usd") or 0
    db.execute(
        "UPDATE runs SET status=?, phase=?, summary=?, error=?, finished_at=? WHERE id=?",
        (status, status, summary, error, db.now(), run_id),
    )
    task_status = {"concluida": "concluida", "cancelada": "nova"}.get(status, "falhou")
    db.execute("UPDATE tasks SET status=? WHERE id=?", (task_status, task_id))
    task = db.query_one("SELECT title FROM tasks WHERE id=?", (task_id,))
    title = task["title"] if task else f"Missão {task_id}"
    if toast:
        if status == "concluida":
            notify.notify(f"Missão concluída: {title}",
                          f"{summary[:160]} (custo: US$ {cost:.2f})")
        elif status == "falhou":
            notify.notify(f"Missão falhou: {title}", (error or summary)[:200])
    if status in ("concluida", "falhou"):
        try:
            from . import whatsapp
            whatsapp.push_result(title, status, (summary or error)[:300], cost,
                                 run_id=run_id)
        except Exception:
            pass
    if status == "concluida" and config.get_setting_bool("proactive_suggestions"):
        threading.Thread(target=_retrospective, args=(run_id, task_id, summary),
                         daemon=True).start()


def _retrospective(run_id: int, task_id: int, summary: str):
    """Proatividade: após concluir, a equipe propõe as próximas missões (sugestões)."""
    try:
        from . import maestro_brain, deliverables
        task = db.query_one("SELECT * FROM tasks WHERE id=?", (task_id,))
        project = db.query_one("SELECT * FROM projects WHERE id=?", (task["project_id"],))
        company = db.query_one("SELECT * FROM companies WHERE id=?", (project["company_id"],))
        if not (task and project and company):
            return
        files = [f["path"] for f in (deliverables.for_run(run_id) or {}).get("files") or []]
        sugg = maestro_brain.suggest_next_missions(company, project, task,
                                                   run_id, summary or "", files)
        if not sugg:
            return
        lines = "\n".join(f"• {s['title']}" for s in sugg)
        notify.notify("💡 A equipe tem sugestões",
                      f"{len(sugg)} próxima(s) missão(ões) sugerida(s) após "
                      f"'{task['title']}'. Veja em Missões.")
        try:
            from . import whatsapp
            whatsapp.broadcast(
                f"💡 *A equipe foi além!* Depois de concluir *{task['title']}*, "
                f"ela sugere as próximas missões:\n{lines}\n\n"
                "Abra o Maestro > Missões para aprovar com um clique.",
                setting_key="wa_notify_result")
        except Exception:
            pass
    except Exception as e:
        db.add_event(f"Retrospectiva falhou (não afeta a missão): {e}",
                     level="warn", run_id=run_id, task_id=task_id)


def _wa_push_approval(approval_id: int):
    try:
        from . import whatsapp
        whatsapp.push_approval(approval_id)
    except Exception:
        pass


# ------------------------------------------------------------------ fases

def _phase_recruit(run_id, task_id, task, project, company):
    _set_phase(run_id, "contratando")
    db.add_event("Maestro avaliando quem contratar para a missão…", run_id, task_id)
    rec = maestro_brain.recruit_agents(company, project, task["mission"], run_id=run_id)
    _add_cost(run_id, rec["cost"])
    agents = maestro_brain.persist_agents(rec["agents"], project["folder"],
                                          company_id=company["id"])
    for a in agents:
        db.execute("INSERT OR IGNORE INTO run_agents(run_id, agent_name) VALUES(?,?)",
                   (run_id, a["name"]))
        tag = "reaproveitado" if a.get("reused") else "contratado"
        spec = " · ".join(filter(None, [a.get("model") or "", a.get("effort") or ""]))
        db.add_event(f"Agente {tag}: {a['name']} ({a.get('role','')})"
                     + (f" [{spec}]" if spec else ""), run_id, task_id)
    _set_phase(run_id, "habilidades")


def _phase_skills(run_id, task_id, project):
    _set_phase(run_id, "habilidades")
    agents = _run_agents(run_id)
    installed = set()
    for a in agents:
        for sk in json.loads(a.get("skills_json") or "[]"):
            key = (sk["owner_repo"], sk["skill"])
            if key in installed:
                continue
            installed.add(key)
            r = skills_catalog.install_skill(project["folder"], sk["owner_repo"], sk["skill"])
            lvl = "info" if r["ok"] else "warn"
            msg = ("Skill instalada: " if r["ok"]
                   else "Falha ao instalar skill (seguindo sem ela): ") + sk["skill"]
            db.add_event(msg, run_id, task_id, level=lvl)
    _set_phase(run_id, "planejando")


def _phase_plan(run_id, task_id, task, project, company) -> bool:
    """Retorna True para seguir à execução; False se pausou/terminou aqui."""
    _set_phase(run_id, "planejando")
    agents = _run_agents(run_id)

    run = db.query_one("SELECT feedback FROM runs WHERE id=?", (run_id,))
    feedback = (run or {}).get("feedback") or ""
    replanned = False
    have = db.query_one("SELECT COUNT(*) AS n FROM microtasks WHERE run_id=?", (run_id,))
    if not have["n"]:
        db.add_event("Maestro quebrando a missão em micro-tarefas (modo PLAN)…"
                     + (" [replanejando com feedback do gestor]" if feedback else ""),
                     run_id, task_id)
        plan = maestro_brain.plan_mission(company, project, task["mission"], agents,
                                          run_id=run_id, feedback=feedback)
        _add_cost(run_id, plan["cost"])
        for m in plan["microtasks"]:
            db.execute(
                "INSERT INTO microtasks(run_id, ord, description, agent_name, priority, parallel, depends_on) "
                "VALUES(?,?,?,?,?,?,?)",
                (run_id, m["ord"], m["description"], m["agent"], m["priority"],
                 1 if m["parallel"] else 0, json.dumps(m["depends_on"])),
            )
        db.add_event(f"Plano pronto: {len(plan['microtasks'])} micro-tarefas.", run_id, task_id)
        if feedback:  # feedback consumido neste replanejamento
            db.execute("UPDATE runs SET feedback='' WHERE id=?", (run_id,))
            replanned = True

    autonomy = int(task.get("autonomy") or 1)

    if autonomy == 0:
        _finish(run_id, task_id, "concluida",
                "Somente sugestão (nível 0): plano gerado; nada foi executado.")
        return False

    if autonomy == 1:
        ap = db.query_one(
            "SELECT * FROM approvals WHERE run_id=? AND kind='plan' ORDER BY id DESC LIMIT 1",
            (run_id,),
        )
        if not ap or (replanned and ap["status"] == "rejeitada"):
            mts = db.query("SELECT ord, description, agent_name FROM microtasks "
                           "WHERE run_id=? ORDER BY ord", (run_id,))
            payload = json.dumps({
                "microtasks": [{"ord": m["ord"], "descricao": m["description"][:200],
                                "agente": m["agent_name"]} for m in mts],
                # equipe com modelo/esforço/skills: aprovar o plano = aprovar
                # a contratação dos agentes E as skills que eles vão usar
                "team": [{"name": a["name"], "role": a.get("role") or "",
                          "model": a.get("model") or "", "effort": a.get("effort") or "",
                          "reports_to": a.get("reports_to") or "",
                          "skills": [s.get("skill") for s in
                                     json.loads(a.get("skills_json") or "[]")]}
                         for a in agents],
            }, ensure_ascii=False)
            aid = governance.create_approval(run_id, task_id, "plan", payload)
            _wa_push_approval(aid)
            _pause_approval(run_id, task_id)
            raise _Paused()
        if ap["status"] == "pendente":
            _pause_approval(run_id, task_id)
            raise _Paused()
        if ap["status"] == "rejeitada":
            _finish(run_id, task_id, "cancelada", "Plano rejeitado pelo gestor.")
            return False
    return True  # autonomy 3 (autônomo) ou plano aprovado


def _phase_execute(run_id, task_id, task, project):
    _set_phase(run_id, "executando")
    agents_by_name = {a["name"]: a for a in _run_agents(run_id)}
    concurrency = config.get_setting_int("max_concurrency")
    max_cost = float(config.get_setting("max_cost_per_run") or 0)

    while True:
        if _cancelled(run_id):
            _finish(run_id, task_id, "cancelada", "Cancelada pelo usuário.")
            return
        if _pause_requested(run_id):
            _pause_manual(run_id, task_id, project["folder"])
            raise _Paused()

        # budget hard-stop (anti-loop): estourou → decisão do gestor
        run = db.query_one("SELECT * FROM runs WHERE id=?", (run_id,))
        if max_cost > 0 and run["total_cost_usd"] >= max_cost and not run["budget_approved"]:
            autonomy = int(task.get("autonomy") or 1)
            if autonomy >= 4:
                # "Só se vive uma vez": auto-aprova o budget e segue (fica auditado)
                db.execute("UPDATE runs SET budget_approved=1 WHERE id=?", (run_id,))
                db.add_event(
                    f"Budget ultrapassado (US$ {run['total_cost_usd']:.2f} ≥ "
                    f"US$ {max_cost:.2f}) — modo 'Só se vive uma vez' auto-aprovou e seguiu.",
                    run_id, task_id, level="warn")
                notify.notify("YOLO: budget auto-aprovado",
                              f"Missão seguiu além do teto de US$ {max_cost:.2f}.")
            else:
                ap = db.query_one(
                    "SELECT id FROM approvals WHERE run_id=? AND kind='budget' AND status='pendente'",
                    (run_id,),
                )
                if not ap:
                    aid = governance.create_approval(
                        run_id, task_id, "budget",
                        json.dumps({"custo_atual": round(run["total_cost_usd"], 2),
                                    "teto_usd": max_cost}, ensure_ascii=False),
                    )
                    _wa_push_approval(aid)
                db.add_event(
                    f"Budget hard-stop: custo US$ {run['total_cost_usd']:.2f} atingiu o teto "
                    f"US$ {max_cost:.2f}. Aguardando decisão.", run_id, task_id, level="warn")
                _pause_approval(run_id, task_id)
                raise _Paused()

        microtasks = db.query("SELECT * FROM microtasks WHERE run_id=? ORDER BY ord", (run_id,))
        pending = [m for m in microtasks if m["status"] == "pendente"]
        if not pending:
            break
        batch = next_batch(microtasks, concurrency)
        if not batch:
            for m in pending:
                db.execute("UPDATE microtasks SET status='bloqueada' WHERE id=?", (m["id"],))
                db.add_event(f"Micro-tarefa #{m['ord']} bloqueada (dependência não concluída).",
                             run_id, task_id, level="warn")
            break

        results = []
        if len(batch) == 1:
            results.append(_run_microtask(run_id, task_id, task, batch[0],
                                          agents_by_name, project["folder"]))
        else:
            db.add_event("Executando em paralelo: "
                         + ", ".join("#" + str(m["ord"]) for m in batch), run_id, task_id)
            with ThreadPoolExecutor(max_workers=len(batch)) as pool:
                futs = [pool.submit(_run_microtask, run_id, task_id, task, m,
                                    agents_by_name, project["folder"]) for m in batch]
                results = [f.result() for f in as_completed(futs)]

        # limite de uso durante o lote → pausa e deixa o heartbeat retomar
        limits = [r["limit_at"] for r in results if r["limit_at"]]
        if limits:
            _pause_limit(run_id, task_id, max(limits))
            raise _Paused()

    rows = db.query("SELECT status FROM microtasks WHERE run_id=?", (run_id,))
    done = sum(1 for r in rows if r["status"] == "concluida")
    summary = f"{done}/{len(rows)} micro-tarefas concluídas."
    if done == len(rows):
        _finish(run_id, task_id, "concluida", summary)
    else:
        _finish(run_id, task_id, "falhou", summary,
                error="Algumas micro-tarefas falharam. Veja o diário de bordo.")


# ------------------------------------------------------------------ execução

def _run_agents(run_id: int) -> list[dict]:
    rows = db.query(
        "SELECT a.* FROM run_agents ra JOIN agents a ON a.name = ra.agent_name "
        "WHERE ra.run_id=? ORDER BY a.name", (run_id,),
    )
    return rows


def compute_ready(microtasks: list[dict]) -> list[dict]:
    done_ords = {m["ord"] for m in microtasks if m["status"] == "concluida"}
    ready = []
    for m in microtasks:
        if m["status"] != "pendente":
            continue
        deps = set(json.loads(m["depends_on"] or "[]"))
        if deps <= done_ords:
            ready.append(m)
    return sorted(ready, key=lambda m: (m["priority"], m["ord"]))


def next_batch(microtasks: list[dict], concurrency: int) -> list[dict]:
    ready = compute_ready(microtasks)
    if not ready:
        return []
    first = ready[0]
    if not first["parallel"]:
        return [first]
    return [m for m in ready if m["parallel"]][:max(1, concurrency)]


def _run_microtask(run_id, task_id, task, m, agents_by_name, folder) -> dict:
    """Retorna {ok, cost, limit_at}. Em limite de uso, devolve a micro-tarefa a 'pendente'."""
    db.execute("UPDATE microtasks SET status='executando', started_at=? WHERE id=?",
               (db.now(), m["id"]))
    db.add_event(f"#{m['ord']} [{m['agent_name']}] {m['description'][:120]}", run_id, task_id)

    deps = json.loads(m["depends_on"] or "[]")
    context = ""
    if deps:
        rows = db.query(
            f"SELECT ord, output FROM microtasks WHERE run_id=? AND ord IN ({','.join('?'*len(deps))})",
            tuple([run_id] + deps),
        )
        parts = [f"- Tarefa #{r['ord']}: {(r['output'] or '')[:500]}" for r in rows]
        context = "\nRESULTADO DAS ETAPAS ANTERIORES:\n" + "\n".join(parts) + "\n"

    agent = agents_by_name.get(m["agent_name"], {})
    skills = [s.get("skill") for s in json.loads(agent.get("skills_json") or "[]") if s.get("skill")]
    skills_line = (
        "SUAS SKILLS (já instaladas em .claude/skills/ neste projeto — USE-AS sempre que "
        "ajudarem no trabalho): " + ", ".join(skills) + "\n"
    ) if skills else ""
    team_line = ", ".join(
        f"{a['name']} ({a.get('role') or ''})" for a in agents_by_name.values()
        if a["name"] != m["agent_name"]
    )
    boss = agent.get("reports_to") or "maestro"
    inbox = db.query(
        "SELECT from_agent, content FROM agent_messages "
        "WHERE run_id=? AND to_agent=? AND kind='mensagem' ORDER BY id DESC LIMIT 5",
        (run_id, m["agent_name"]),
    )
    inbox_block = ""
    if inbox:
        inbox_block = ("\nMENSAGENS RECEBIDAS DE COLEGAS:\n"
                       + "\n".join(f"- de {r['from_agent']}: {r['content'][:300]}" for r in inbox)
                       + "\n")
    prompt = (
        f"MISSÃO GERAL: {task['mission']}\n"
        f"{context}{inbox_block}\n"
        f"SUA MICRO-TAREFA AGORA (#{m['ord']}): {m['description']}\n\n"
        f"{skills_line}"
        f"SUA EQUIPE (colegas): {team_line or '(você trabalha sozinho nesta missão)'}. "
        f"Seu líder direto: {boss}.\n"
        "COMUNICAÇÃO COM A EQUIPE — ao final da sua resposta você PODE (quando necessário):\n"
        "- avisar/pedir algo a um colega escrevendo uma linha exata: "
        "[MENSAGEM PARA: slug-do-colega] texto da mensagem\n"
        "- criar uma tarefa de backlog para um colega escrevendo uma linha exata: "
        "[BACKLOG PARA: slug-do-colega] descrição completa e autossuficiente da tarefa\n"
        "Use backlog somente para trabalho realmente necessário que você descobriu agora "
        "e que não está no plano.\n\n"
        "Regras: se existir um arquivo MEMORY.md no projeto, leia-o antes de começar "
        "(é a memória da missão); trabalhe no diretório atual do projeto; execute SOMENTE "
        "esta micro-tarefa; não pergunte nada ao usuário; ao terminar, escreva um resumo "
        "curto (2-3 frases) do que foi feito."
    )
    # modelo/esforço do agente (escolhidos pela IA), sempre limitados pelos tetos
    model = config.clamp_model(agent.get("model") or config.get_setting("model"))
    effort = config.clamp_effort(agent.get("effort") or "medium")
    res = claude_cli.run_claude(
        prompt,
        cwd=folder,
        model=model,
        system_prompt=agent.get("system_prompt") or None,
        yolo=config.get_setting_bool("yolo"),
        max_turns=config.get_setting_int("max_turns_exec"),
        timeout_min=config.get_setting_int("task_timeout_min"),
        effort=effort,
        log_ctx={"run_id": run_id, "microtask_id": m["id"], "kind": "executar",
                 "agent_name": m["agent_name"]},
    )
    _add_cost(run_id, res["cost_usd"])

    limit_at = claude_cli.detect_limit(res)
    if limit_at:
        db.execute("UPDATE microtasks SET status='pendente', started_at=NULL WHERE id=?",
                   (m["id"],))
        return {"ok": False, "cost": res["cost_usd"], "limit_at": limit_at}

    status = "concluida" if res["ok"] else "falhou"
    output = res["result"] if res["ok"] else (res["error"] or res["result"])
    if res["ok"]:
        _process_team_comms(run_id, task_id, m, output or "", agents_by_name)
    db.execute(
        "UPDATE microtasks SET status=?, output=?, cost_usd=?, finished_at=? WHERE id=?",
        (status, (output or "")[:8000], res["cost_usd"], db.now(), m["id"]),
    )
    db.add_event(f"#{m['ord']} {status} (US$ {res['cost_usd']:.2f})",
                 run_id, task_id, level="info" if res["ok"] else "error")
    return {"ok": res["ok"], "cost": res["cost_usd"], "limit_at": None}


_COMM_RE = re.compile(r"^\s*\[(MENSAGEM|BACKLOG) PARA:\s*([\w.-]+)\]\s*(.+)$",
                      re.IGNORECASE | re.MULTILINE)


def _process_team_comms(run_id, task_id, m, output: str, agents_by_name: dict):
    """Agentes conversam e geram backlog uns para os outros (como numa empresa).

    Linhas [MENSAGEM PARA: x] viram mensagens; [BACKLOG PARA: x] viram novas
    micro-tarefas pendentes atribuídas ao colega (com teto anti-loop).
    """
    with _state_lock:  # serializa criação de ords entre micro-tarefas paralelas
        for kind, to_agent, content in _COMM_RE.findall(output):
            to_agent = to_agent.lower()
            if to_agent not in agents_by_name or to_agent == m["agent_name"]:
                continue
            content = content.strip()[:2000]
            if not content:
                continue
            is_backlog = kind.upper() == "BACKLOG"
            db.execute(
                "INSERT INTO agent_messages(run_id, ts, from_agent, to_agent, kind, content) "
                "VALUES(?,?,?,?,?,?)",
                (run_id, db.now(), m["agent_name"], to_agent,
                 "backlog" if is_backlog else "mensagem", content),
            )
            if is_backlog:
                total = db.query_one("SELECT COUNT(*) n FROM microtasks WHERE run_id=?",
                                     (run_id,))["n"]
                if total >= MAX_MICROTASKS_PER_RUN:
                    db.add_event(f"Backlog de {m['agent_name']} para {to_agent} ignorado: "
                                 f"teto de {MAX_MICROTASKS_PER_RUN} micro-tarefas atingido.",
                                 run_id, task_id, level="warn")
                    continue
                nxt = db.query_one("SELECT COALESCE(MAX(ord),0)+1 o FROM microtasks "
                                   "WHERE run_id=?", (run_id,))["o"]
                db.execute(
                    "INSERT INTO microtasks(run_id, ord, description, agent_name, priority, "
                    "parallel, depends_on) VALUES(?,?,?,?,?,?,?)",
                    (run_id, nxt, f"[Backlog criado por {m['agent_name']}] {content}",
                     to_agent, 9, 1, json.dumps([m["ord"]])),
                )
                db.add_event(f"{m['agent_name']} criou backlog #{nxt} para {to_agent}: "
                             f"{content[:120]}", run_id, task_id)
            else:
                db.add_event(f"💬 {m['agent_name']} → {to_agent}: {content[:150]}",
                             run_id, task_id)
            try:
                from . import whatsapp
                whatsapp.push_team(m["agent_name"], to_agent,
                                   "backlog" if is_backlog else "mensagem", content)
            except Exception:
                pass
