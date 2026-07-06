"""Bot de menus do WhatsApp — opera 100% do Maestro por cadeia de opções (sem IA).

Máquina de estados por contato (tabela wa_sessions). Convenções:
- números escolhem opções; `0` volta; `menu` recomeça do menu principal;
- sessão expira em 10 minutos (volta ao menu);
- autorização: número desconhecido precisa enviar o código de 6 dígitos.
"""
import os
import re
import json
import datetime

from . import db, config, governance, orchestrator, bootstrap

SESSION_TIMEOUT_MIN = 10

AUTONOMY_OPTS = [  # (opção exibida, nível)
    ("Aprovação obrigatória (padrão seguro)", 1),
    ("Somente sugestão (só planeja)", 0),
    ("Autônomo (exige aceitar termo de risco)", 3),
    ("Só se vive uma vez — YOLO total (exige termo)", 4),
]

STATUS_PT = {
    "nova": "Nova", "executando": "🔵 Executando", "aguardando": "🟡 Aguardando",
    "concluida": "✅ Concluída", "falhou": "❌ Falhou", "pausada": "⏸ Pausada",
    "aguardando_aprovacao": "🟡 Aguardando aprovação",
    "aguardando_limite": "🕐 Aguardando limite", "cancelada": "Cancelada",
    "pendente": "Pendente", "bloqueada": "Bloqueada", "preparando": "🔵 Preparando",
}


def _st(s): return STATUS_PT.get(s, s)


# ------------------------------------------------------------ sessão

def _load_session(phone: str) -> dict:
    row = db.query_one("SELECT * FROM wa_sessions WHERE phone=?", (phone,))
    if not row:
        return {"state": "menu", "ctx": {}}
    try:
        age = datetime.datetime.now() - datetime.datetime.fromisoformat(row["updated_at"])
        if age.total_seconds() > SESSION_TIMEOUT_MIN * 60:
            return {"state": "menu", "ctx": {}}
    except ValueError:
        pass
    try:
        ctx = json.loads(row["context"] or "{}")
    except json.JSONDecodeError:
        ctx = {}
    return {"state": row["state"] or "menu", "ctx": ctx}


def set_session(phone: str, state: str, ctx: dict | None = None):
    db.execute(
        "INSERT INTO wa_sessions(phone, state, context, updated_at) VALUES(?,?,?,?) "
        "ON CONFLICT(phone) DO UPDATE SET state=excluded.state, "
        "context=excluded.context, updated_at=excluded.updated_at",
        (phone, state, json.dumps(ctx or {}, ensure_ascii=False), db.now()),
    )


def _company(ctx: dict) -> dict | None:
    cid = ctx.get("company_id")
    c = db.query_one("SELECT * FROM companies WHERE id=?", (cid,)) if cid else None
    if not c:
        c = db.query_one("SELECT * FROM companies ORDER BY id LIMIT 1")
        if c:
            ctx["company_id"] = c["id"]
    return c


# ------------------------------------------------------------ entrada

def handle(phone: str, text: str) -> list[str]:
    """Processa a mensagem e retorna a(s) resposta(s)."""
    text = (text or "").strip()
    if not db.query_one("SELECT phone FROM wa_operators WHERE phone=?", (phone,)):
        return _handle_auth(phone, text)

    sess = _load_session(phone)
    if text.lower() in ("menu", "oi", "olá", "ola", "inicio", "início"):
        set_session(phone, "menu", sess["ctx"])
        return [_menu(sess["ctx"])]

    fn = _STATES.get(sess["state"], _s_menu)
    return fn(phone, text, sess["ctx"])


def _handle_auth(phone: str, text: str) -> list[str]:
    from . import whatsapp
    if re.fullmatch(r"\d{6}", text) and text == whatsapp.get_secret():
        db.execute("INSERT OR IGNORE INTO wa_operators(phone, name, created_at) VALUES(?,?,?)",
                   (phone, "", db.now()))
        db.add_event(f"Operador WhatsApp autorizado: {phone}", level="warn")
        ctx = {}
        _company(ctx)
        set_session(phone, "menu", ctx)
        return ["✅ Número autorizado! Você agora opera o Claude Maestro por aqui.",
                _menu(ctx)]
    return ["🔒 Este é o Claude Maestro. Para operar, envie o *código de 6 dígitos* "
            "que aparece na tela WhatsApp do aplicativo."]


# ------------------------------------------------------------ menu principal

def _menu(ctx: dict) -> str:
    c = _company(ctx)
    pend = db.query_one("SELECT COUNT(*) n FROM approvals WHERE status='pendente'")["n"]
    nsugg = db.query_one(
        "SELECT COUNT(*) n FROM suggestions WHERE status='pendente'"
        + (" AND company_id=?" if c else ""), ((c["id"],) if c else ()))["n"]
    paused = orchestrator.paused_count()
    return (f"🎼 *Claude Maestro*\n"
            f"Empresa ativa: *{c['name'] if c else '(nenhuma — crie na opção 3)'}*\n\n"
            "1. 🚀 Missões\n"
            f"2. ✅ Aprovações pendentes ({pend})\n"
            "3. 🏢 Empresas\n"
            "4. 📁 Projetos\n"
            "5. 👥 Equipe (quem está trabalhando)\n"
            "6. 📊 Painel\n"
            + (f"7. ▶️ Retomar empresa ({paused} pausada(s))\n" if paused
               else "7. ⏸ Pausar empresa\n")
            + "8. 🔔 Notificações\n"
            + f"9. 💡 Sugestões da equipe ({nsugg})\n\n"
            "_Responda com o número. A qualquer momento: *0* volta, *menu* recomeça._")


def _s_menu(phone, text, ctx) -> list[str]:
    op = text.strip()
    if op == "1":
        return _show_missions(phone, ctx)
    if op == "2":
        return _show_approvals(phone, ctx)
    if op == "3":
        return _show_companies(phone, ctx)
    if op == "4":
        return _show_projects(phone, ctx)
    if op == "5":
        set_session(phone, "menu", ctx)
        return [_team_text(), _menu(ctx)]
    if op == "6":
        set_session(phone, "menu", ctx)
        return [_dash_text(ctx), _menu(ctx)]
    if op == "7":
        set_session(phone, "menu", ctx)
        if orchestrator.paused_count():
            n = orchestrator.resume_company()
            return [f"▶️ Empresa retomada — {n} execução(ões) voltaram ao trabalho.", _menu(ctx)]
        n = orchestrator.pause_company()
        return [(f"⏸ Pausando {n} execução(ões) — efetiva ao fim da micro-tarefa atual. "
                 "A memória fica salva em MEMORY.md.") if n
                else "Nada executando agora para pausar.", _menu(ctx)]
    if op == "8":
        return _show_notif(phone, ctx)
    if op == "9":
        return _show_suggestions(phone, ctx)
    return [_menu(ctx)]


# ------------------------------------------------------------ sugestões da equipe

def _show_suggestions(phone, ctx) -> list[str]:
    c = _company(ctx)
    rows = db.query(
        "SELECT * FROM suggestions WHERE status='pendente'"
        + (" AND company_id=?" if c else "") + " ORDER BY id DESC LIMIT 9",
        ((c["id"],) if c else ()))
    if not rows:
        set_session(phone, "menu", ctx)
        return ["💡 Nenhuma sugestão pendente. Elas aparecem aqui quando a equipe "
                "conclui missões e enxerga próximos passos.", _menu(ctx)]
    ctx["sugg_ids"] = [r["id"] for r in rows]
    set_session(phone, "sugg_list", ctx)
    lines = [f"{i+1}. {r['title']}" for i, r in enumerate(rows)]
    return ["💡 *Sugestões da equipe* — escolha para ver:\n" + "\n".join(lines) + "\n0. Voltar"]


def _s_sugg_list(phone, text, ctx) -> list[str]:
    if text == "0":
        set_session(phone, "menu", ctx)
        return [_menu(ctx)]
    try:
        ctx["sugg_id"] = (ctx.get("sugg_ids") or [])[int(text) - 1]
    except (ValueError, IndexError):
        return ["Opção inválida."]
    s = db.query_one("SELECT * FROM suggestions WHERE id=?", (ctx["sugg_id"],))
    if not s:
        return _show_suggestions(phone, ctx)
    set_session(phone, "sugg_decide", ctx)
    return [f"💡 *{s['title']}*\n"
            + (f"_Por quê: {s['reason']}_\n\n" if s["reason"] else "\n")
            + s["mission"][:600]
            + "\n\n1. ▶️ Aceitar e executar agora\n2. 💾 Aceitar (executo depois)\n"
              "3. 🗑 Descartar\n0. Voltar"]


def _s_sugg_decide(phone, text, ctx) -> list[str]:
    sid = ctx.get("sugg_id")
    if text == "0":
        return _show_suggestions(phone, ctx)
    if text == "3":
        db.execute("UPDATE suggestions SET status='descartada' WHERE id=? AND status='pendente'",
                   (sid,))
        return ["🗑 Sugestão descartada."] + _show_suggestions(phone, ctx)
    if text not in ("1", "2"):
        return ["Responda 1, 2, 3 ou 0."]
    s = db.query_one("SELECT * FROM suggestions WHERE id=? AND status='pendente'", (sid,))
    if not s:
        return ["Esta sugestão já foi resolvida."] + _show_suggestions(phone, ctx)
    tid = db.execute(
        "INSERT INTO tasks(project_id, title, mission, mode, autonomy, status, created_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (s["project_id"], s["title"], s["mission"], "yolo", 1, "nova", db.now()))
    db.execute("UPDATE suggestions SET status='aceita' WHERE id=?", (sid,))
    db.add_event(f"Sugestão da equipe aceita via WhatsApp: {s['title']}", task_id=tid)
    msgs = [f"✅ Missão *{s['title']}* criada."]
    if text == "1":
        try:
            orchestrator.start_task(tid)
            msgs.append("▶️ Executando! Te aviso quando o plano estiver pronto para aprovar.")
        except ValueError as e:
            msgs.append(f"⚠️ {e}")
    ctx["task_id"] = tid
    return msgs + _task_view(phone, ctx)


# ------------------------------------------------------------ missões

def _show_missions(phone, ctx) -> list[str]:
    c = _company(ctx)
    tasks = db.query(
        "SELECT t.* FROM tasks t JOIN projects p ON p.id=t.project_id "
        "WHERE p.company_id=? ORDER BY t.id DESC LIMIT 15", (c["id"],)) if c else []
    ctx["ids"] = [t["id"] for t in tasks]
    set_session(phone, "missions", ctx)
    lines = [f"{i+1}. {t['title']} — {_st(t['status'])}" for i, t in enumerate(tasks)]
    n_new = len(tasks) + 1
    return ["🚀 *Missões*\n" + ("\n".join(lines) + "\n" if lines else "(nenhuma ainda)\n")
            + f"\n{n_new}. ➕ Criar nova missão\n0. Voltar"]


def _s_missions(phone, text, ctx) -> list[str]:
    if text == "0":
        set_session(phone, "menu", ctx)
        return [_menu(ctx)]
    ids = ctx.get("ids") or []
    try:
        n = int(text)
    except ValueError:
        return ["Responda com o número de uma opção."]
    if n == len(ids) + 1:
        return _new_mission_start(phone, ctx)
    if 1 <= n <= len(ids):
        ctx["task_id"] = ids[n - 1]
        return _task_view(phone, ctx)
    return ["Opção inválida."]


def _task_view(phone, ctx) -> list[str]:
    t = db.query_one(
        "SELECT t.*, p.name AS project_name FROM tasks t "
        "JOIN projects p ON p.id=t.project_id WHERE t.id=?", (ctx["task_id"],))
    if not t:
        set_session(phone, "menu", ctx)
        return ["Missão não encontrada.", _menu(ctx)]
    run = db.query_one("SELECT * FROM runs WHERE task_id=? ORDER BY id DESC LIMIT 1",
                       (t["id"],))
    body = [f"🚀 *{t['title']}*", f"Projeto: {t['project_name']}",
            f"Estado: {_st(t['status'])}"]
    if run:
        body.append(f"Última execução: {_st(run['status'])} "
                    f"(custo US$ {run['total_cost_usd']:.2f})")
        mts = db.query("SELECT ord, status, agent_name, description FROM microtasks "
                       "WHERE run_id=? ORDER BY ord", (run["id"],))
        if mts:
            body.append("\n*Micro-tarefas:*")
            for m in mts[:12]:
                body.append(f"  #{m['ord']} {_st(m['status'])} [{m['agent_name']}] "
                            f"{m['description'][:60]}")
    running = orchestrator.is_task_running(t["id"])
    opts = ["\n1. ▶️ Executar" if not running else "\n1. 🔄 Atualizar status"]
    if running and run:
        opts.append("2. 🛑 Cancelar execução")
    opts.append("0. Voltar")
    set_session(phone, "task_view", ctx)
    return ["\n".join(body) + "\n" + "\n".join(opts)]


def _s_task_view(phone, text, ctx) -> list[str]:
    if text == "0":
        return _show_missions(phone, ctx)
    t_id = ctx.get("task_id")
    running = orchestrator.is_task_running(t_id)
    if text == "1":
        if running:
            return _task_view(phone, ctx)
        try:
            orchestrator.start_task(t_id)
            return ["▶️ Missão iniciada! Te aviso por aqui quando precisar de aprovação "
                    "ou quando terminar."] + _task_view(phone, ctx)
        except ValueError as e:
            return [f"⚠️ {e}"] + _task_view(phone, ctx)
    if text == "2" and running:
        run = db.query_one("SELECT id FROM runs WHERE task_id=? ORDER BY id DESC LIMIT 1",
                           (t_id,))
        if run:
            orchestrator.cancel_run(run["id"])
        return ["🛑 Cancelamento solicitado."] + _task_view(phone, ctx)
    return ["Opção inválida."]


def _new_mission_start(phone, ctx) -> list[str]:
    c = _company(ctx)
    if not c:
        set_session(phone, "menu", ctx)
        return ["Crie primeiro uma empresa (opção 3 do menu).", _menu(ctx)]
    projects = db.query("SELECT * FROM projects WHERE company_id=? ORDER BY id DESC",
                        (c["id"],))
    if not projects:
        set_session(phone, "menu", ctx)
        return ["Crie primeiro um projeto (opção 4 do menu).", _menu(ctx)]
    ctx["proj_ids"] = [p["id"] for p in projects]
    set_session(phone, "nm_project", ctx)
    lines = [f"{i+1}. {p['name']}" for i, p in enumerate(projects)]
    return ["➕ *Nova missão* — em qual projeto?\n" + "\n".join(lines) + "\n0. Voltar"]


def _s_nm_project(phone, text, ctx) -> list[str]:
    if text == "0":
        return _show_missions(phone, ctx)
    try:
        n = int(text)
        ctx["nm_project_id"] = (ctx.get("proj_ids") or [])[n - 1]
    except (ValueError, IndexError):
        return ["Opção inválida."]
    from . import mission_templates
    tpls = mission_templates.list_templates()
    set_session(phone, "nm_template", ctx)
    lines = [f"{i+1}. {t['title']}" for i, t in enumerate(tpls)]
    n_free = len(tpls) + 1
    return ["✨ *Modelos prontos* — escolha um ou escreva a sua:\n"
            + "\n".join(lines) + f"\n{n_free}. ✏️ Escrever minha própria missão\n0. Voltar"]


def _s_nm_template(phone, text, ctx) -> list[str]:
    if text == "0":
        return _show_missions(phone, ctx)
    from . import mission_templates
    tpls = mission_templates.list_templates()
    try:
        n = int(text)
    except ValueError:
        return ["Responda com o número de uma opção."]
    if n == len(tpls) + 1:
        set_session(phone, "nm_title", ctx)
        return ["Qual o *nome da missão*? (ex.: Criar o site da padaria)"]
    if 1 <= n <= len(tpls):
        t = tpls[n - 1]
        ctx["nm_title"] = t["title"]
        ctx["nm_mission"] = t["mission"]
        set_session(phone, "nm_autonomy", ctx)
        lines = [f"{i+1}. {label}" for i, (label, _) in enumerate(AUTONOMY_OPTS)]
        return [f"✨ Modelo *{t['title']}* selecionado.",
                "Nível de *autonomia*?\n" + "\n".join(lines) + "\n0. Voltar"]
    return ["Opção inválida."]


def _s_nm_title(phone, text, ctx) -> list[str]:
    if text == "0":
        return _show_missions(phone, ctx)
    ctx["nm_title"] = text[:200]
    set_session(phone, "nm_desc", ctx)
    return ["Agora descreva *o que deve ser feito* (com suas palavras, uma mensagem só):"]


def _s_nm_desc(phone, text, ctx) -> list[str]:
    if text == "0":
        return _show_missions(phone, ctx)
    ctx["nm_mission"] = text[:4000]
    set_session(phone, "nm_autonomy", ctx)
    lines = [f"{i+1}. {label}" for i, (label, _) in enumerate(AUTONOMY_OPTS)]
    return ["Nível de *autonomia*?\n" + "\n".join(lines) + "\n0. Voltar"]


def _s_nm_autonomy(phone, text, ctx) -> list[str]:
    if text == "0":
        return _show_missions(phone, ctx)
    try:
        level = AUTONOMY_OPTS[int(text) - 1][1]
    except (ValueError, IndexError):
        return ["Opção inválida."]
    ctx["nm_autonomy"] = level
    if level >= 2:
        set_session(phone, "nm_risk", ctx)
        return ["⚠️ *Termo de Ciência e Assunção de Risco*\n\n"
                + governance.RISK_TERM
                + "\nPara aceitar e continuar, responda exatamente: *ACEITO*\n0. Voltar"]
    return _nm_confirm(phone, ctx)


def _s_nm_risk(phone, text, ctx) -> list[str]:
    if text == "0":
        return _show_missions(phone, ctx)
    if text.strip().upper() != "ACEITO":
        return ["Para o modo autônomo, responda *ACEITO* (ou 0 para voltar)."]
    ctx["nm_risk_ok"] = True
    return _nm_confirm(phone, ctx)


def _nm_confirm(phone, ctx) -> list[str]:
    set_session(phone, "nm_run", ctx)
    return [f"Missão: *{ctx['nm_title']}*\n\n1. ▶️ Criar e executar agora\n"
            "2. 💾 Só salvar (executo depois)\n0. Cancelar"]


def _s_nm_run(phone, text, ctx) -> list[str]:
    if text == "0":
        return _show_missions(phone, ctx)
    if text not in ("1", "2"):
        return ["Responda 1, 2 ou 0."]
    level = int(ctx.get("nm_autonomy") or 1)
    tid = db.execute(
        "INSERT INTO tasks(project_id, title, mission, mode, autonomy, status, created_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (ctx["nm_project_id"], ctx["nm_title"], ctx["nm_mission"], "yolo",
         level, "nova", db.now()),
    )
    if level >= 2:
        governance.record_consent(f"task:{tid}", level,
                                  details=f"{ctx['nm_title']} (via WhatsApp {phone})")
    msgs = [f"💾 Missão *{ctx['nm_title']}* criada."]
    if text == "1":
        try:
            orchestrator.start_task(tid)
            msgs.append("▶️ Executando! Te aviso por aqui quando o plano estiver pronto "
                        "para você aprovar." if level == 1 else
                        "▶️ Executando! Te aviso quando terminar.")
        except ValueError as e:
            msgs.append(f"⚠️ {e}")
    for k in ("nm_project_id", "nm_title", "nm_mission", "nm_autonomy", "nm_risk_ok"):
        ctx.pop(k, None)
    ctx["task_id"] = tid
    return msgs + _task_view(phone, ctx)


# ------------------------------------------------------------ aprovações

def approval_message(approval_id: int) -> str:
    """Texto da aprovação (usado também no push proativo)."""
    a = db.query_one(
        "SELECT ap.*, t.title FROM approvals ap JOIN tasks t ON t.id=ap.task_id "
        "WHERE ap.id=?", (approval_id,))
    if not a:
        return ""
    lines = [f"🔔 *Decisão necessária* — missão *{a['title']}*"]
    try:
        data = json.loads(a["payload"] or "null")
    except json.JSONDecodeError:
        data = None
    if a["kind"] == "plan" and isinstance(data, dict):
        team = data.get("team") or []
        if team:
            lines.append("\n*Equipe (contratação + skills):*")
            for t in team:
                sk = ", ".join(t.get("skills") or []) or "sem skills"
                boss = f" · reporta a {t['reports_to']}" if t.get("reports_to") else ""
                lines.append(f"• {t['name']} ({t.get('role') or ''}) — {t.get('model')}"
                             f" · skills: {sk}{boss}")
        mts = data.get("microtasks") or []
        if mts:
            lines.append("\n*Plano:*")
            for m in mts[:12]:
                lines.append(f"{m['ord']}. [{m['agente']}] {m['descricao'][:100]}")
    elif a["kind"] == "budget" and isinstance(data, dict):
        lines.append(f"\n💸 *Budget*: custo atual US$ {data.get('custo_atual')} atingiu "
                     f"o teto US$ {data.get('teto_usd')}. Aprovar libera continuar.")
    lines.append("\n1. ✅ Aprovar\n2. ❌ Reprovar (te pergunto o motivo e replanejo)\n"
                 "3. 🛑 Cancelar a missão inteira\n0. Ignorar por agora")
    return "\n".join(lines)


def _show_approvals(phone, ctx) -> list[str]:
    rows = db.query("SELECT ap.id, t.title, ap.kind FROM approvals ap "
                    "JOIN tasks t ON t.id=ap.task_id WHERE ap.status='pendente' "
                    "ORDER BY ap.id LIMIT 10")
    if not rows:
        set_session(phone, "menu", ctx)
        return ["✅ Nenhuma decisão pendente. Tudo em dia.", _menu(ctx)]
    if len(rows) == 1:
        ctx["aid"] = rows[0]["id"]
        set_session(phone, "ap_decide", ctx)
        return [approval_message(rows[0]["id"])]
    ctx["ap_ids"] = [r["id"] for r in rows]
    set_session(phone, "ap_list", ctx)
    lines = [f"{i+1}. {r['title']} ({'plano' if r['kind']=='plan' else 'budget'})"
             for i, r in enumerate(rows)]
    return ["✅ *Aprovações pendentes*\n" + "\n".join(lines) + "\n0. Voltar"]


def _s_ap_list(phone, text, ctx) -> list[str]:
    if text == "0":
        set_session(phone, "menu", ctx)
        return [_menu(ctx)]
    try:
        ctx["aid"] = (ctx.get("ap_ids") or [])[int(text) - 1]
    except (ValueError, IndexError):
        return ["Opção inválida."]
    set_session(phone, "ap_decide", ctx)
    return [approval_message(ctx["aid"])]


def _s_ap_decide(phone, text, ctx) -> list[str]:
    aid = ctx.get("aid")
    if text == "0":
        set_session(phone, "menu", ctx)
        return [_menu(ctx)]
    if text == "1":
        a = orchestrator.apply_approval_decision(aid, True)
        set_session(phone, "menu", ctx)
        return [("✅ Aprovado! A equipe está executando." if a
                 else "Esta decisão já tinha sido resolvida."), _menu(ctx)]
    if text == "2":
        set_session(phone, "ap_reason", ctx)
        return ["Escreva o *motivo da reprovação* (o Maestro usa isso para "
                "refazer o plano):"]
    if text == "3":
        a = orchestrator.apply_approval_decision(
            aid, False, reason="Missão cancelada pelo gestor via WhatsApp.", cancel=True)
        set_session(phone, "menu", ctx)
        return [("🛑 Missão cancelada." if a
                 else "Esta decisão já tinha sido resolvida."), _menu(ctx)]
    return ["Responda 1 (aprovar), 2 (reprovar), 3 (cancelar) ou 0."]


def _s_ap_reason(phone, text, ctx) -> list[str]:
    if text == "0":
        ctx2 = dict(ctx)
        set_session(phone, "ap_decide", ctx2)
        return [approval_message(ctx.get("aid"))]
    a = orchestrator.apply_approval_decision(ctx.get("aid"), False, reason=text[:2000])
    set_session(phone, "menu", ctx)
    return [("❌ Reprovado. O Maestro está *replanejando* com o seu feedback — "
             "te mando o novo plano quando estiver pronto." if a
             else "Esta decisão já tinha sido resolvida."), _menu(ctx)]


# ------------------------------------------------------------ empresas

def _show_companies(phone, ctx) -> list[str]:
    rows = db.query("SELECT * FROM companies ORDER BY id")
    ctx["comp_ids"] = [r["id"] for r in rows]
    set_session(phone, "companies", ctx)
    cur = ctx.get("company_id")
    lines = [f"{i+1}. {r['name']}{' ⭐ (ativa)' if r['id'] == cur else ''}"
             for i, r in enumerate(rows)]
    n_new = len(rows) + 1
    return ["🏢 *Empresas* — escolha um número para *tornar ativa*:\n"
            + ("\n".join(lines) + "\n" if lines else "(nenhuma ainda)\n")
            + f"\n{n_new}. ➕ Criar empresa\n0. Voltar"]


def _s_companies(phone, text, ctx) -> list[str]:
    if text == "0":
        set_session(phone, "menu", ctx)
        return [_menu(ctx)]
    ids = ctx.get("comp_ids") or []
    try:
        n = int(text)
    except ValueError:
        return ["Opção inválida."]
    if n == len(ids) + 1:
        set_session(phone, "ce_name", ctx)
        return ["Qual o *nome da empresa*?"]
    if 1 <= n <= len(ids):
        ctx["company_id"] = ids[n - 1]
        set_session(phone, "menu", ctx)
        return ["⭐ Empresa ativa alterada.", _menu(ctx)]
    return ["Opção inválida."]


def _s_ce_name(phone, text, ctx) -> list[str]:
    if text == "0":
        return _show_companies(phone, ctx)
    ctx["ce_name"] = text[:200]
    set_session(phone, "ce_desc", ctx)
    return ["O que a empresa faz e o que você quer melhorar? (uma mensagem só)"]


def _s_ce_desc(phone, text, ctx) -> list[str]:
    if text == "0":
        return _show_companies(phone, ctx)
    cid = db.execute(
        "INSERT INTO companies(name, objectives, impediments, problems, extra_json, created_at) "
        "VALUES(?,?,?,?,?,?)",
        (ctx.pop("ce_name"), text[:2000], "", "", "", db.now()),
    )
    ctx["company_id"] = cid
    set_session(phone, "menu", ctx)
    return ["🏢 Empresa criada e definida como ativa!", _menu(ctx)]


# ------------------------------------------------------------ projetos

def _safe_name(s: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "-", s).strip() or "projeto"


def _show_projects(phone, ctx) -> list[str]:
    c = _company(ctx)
    if not c:
        set_session(phone, "menu", ctx)
        return ["Crie primeiro uma empresa (opção 3).", _menu(ctx)]
    rows = db.query("SELECT * FROM projects WHERE company_id=? ORDER BY id DESC", (c["id"],))
    set_session(phone, "projects", ctx)
    lines = [f"{i+1}. {r['name']}" for i, r in enumerate(rows)]
    ctx["proj_count"] = len(rows)
    set_session(phone, "projects", ctx)
    return [f"📁 *Projetos de {c['name']}*\n"
            + ("\n".join(lines) + "\n" if lines else "(nenhum ainda)\n")
            + f"\n{len(rows)+1}. ➕ Criar projeto\n0. Voltar"]


def _s_projects(phone, text, ctx) -> list[str]:
    if text == "0":
        set_session(phone, "menu", ctx)
        return [_menu(ctx)]
    try:
        n = int(text)
    except ValueError:
        return ["Opção inválida."]
    if n == int(ctx.get("proj_count") or 0) + 1:
        set_session(phone, "cp_name", ctx)
        return ["Qual o *nome do projeto*? (a pasta é criada automaticamente em "
                "Documentos\\Maestro)"]
    set_session(phone, "menu", ctx)
    return [_menu(ctx)]


def _s_cp_name(phone, text, ctx) -> list[str]:
    if text == "0":
        return _show_projects(phone, ctx)
    c = _company(ctx)
    name = text[:200]
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    folder = os.path.join(docs, "Maestro", _safe_name(c["name"]), _safe_name(name))
    os.makedirs(folder, exist_ok=True)
    pid = db.execute(
        "INSERT INTO projects(company_id, name, folder, description, created_at) "
        "VALUES(?,?,?,?,?)", (c["id"], name, folder, "criado via WhatsApp", db.now()),
    )
    project = db.query_one("SELECT * FROM projects WHERE id=?", (pid,))
    bootstrap.write_project_claude_md(project, c)
    set_session(phone, "menu", ctx)
    return [f"📁 Projeto *{name}* criado!\nPasta: {folder}", _menu(ctx)]


# ------------------------------------------------------------ equipe / painel / notificações

def _team_text() -> str:
    org = db.query("SELECT name, role, reports_to, skills_json FROM agents ORDER BY name")
    if not org:
        return "👥 Nenhum agente contratado ainda."
    live = db.query(
        "SELECT m.agent_name, m.ord, t.title FROM microtasks m "
        "JOIN runs r ON r.id=m.run_id JOIN tasks t ON t.id=r.task_id "
        "WHERE m.status='executando' AND r.status IN ('executando','preparando')")
    working = {r["agent_name"]: r for r in live}
    lines = ["👥 *Equipe de agentes*"]
    for a in org:
        w = working.get(a["name"])
        st = (f"🟢 trabalhando na #{w['ord']} ({w['title'][:40]})" if w else "⚪ disponível")
        boss = f" · líder: {a['reports_to']}" if a["reports_to"] else ""
        lines.append(f"• *{a['name']}* ({a['role'][:40]}){boss} — {st}")
    msgs = db.query("SELECT * FROM agent_messages ORDER BY id DESC LIMIT 5")
    if msgs:
        lines.append("\n*Últimas conversas da equipe:*")
        for msg in reversed(msgs):
            icon = "🗒️" if msg["kind"] == "backlog" else "💬"
            lines.append(f"{icon} {msg['from_agent']} → {msg['to_agent']}: "
                         f"{msg['content'][:80]}")
    return "\n".join(lines)


def _dash_text(ctx) -> str:
    c = _company(ctx)
    where = "WHERE p.company_id=?" if c else ""
    params = (c["id"],) if c else ()
    tasks = db.query_one(f"SELECT COUNT(*) n FROM tasks t JOIN projects p "
                         f"ON p.id=t.project_id {where}", params)["n"]
    running = db.query_one(
        "SELECT COUNT(*) n FROM runs WHERE status IN ('executando','preparando')")["n"]
    pend = db.query_one("SELECT COUNT(*) n FROM approvals WHERE status='pendente'")["n"]
    cost = db.query_one("SELECT COALESCE(SUM(total_cost_usd),0) c FROM runs")["c"]
    return (f"📊 *Painel*\nMissões: {tasks}\nEm execução: {running}\n"
            f"Aguardando decisão: {pend}\nPausadas: {orchestrator.paused_count()}\n"
            f"Custo acumulado: US$ {cost:.2f}")


_NOTIF_KEYS = [
    ("wa_notify_approval", "Aprovações pendentes"),
    ("wa_notify_result", "Missão concluída/falhou"),
    ("wa_notify_team", "Conversa/backlog da equipe"),
]


def _show_notif(phone, ctx) -> list[str]:
    set_session(phone, "notif", ctx)
    lines = [f"{i+1}. {'🔔' if config.get_setting_bool(k) else '🔕'} {label}"
             for i, (k, label) in enumerate(_NOTIF_KEYS)]
    return ["🔔 *Notificações* — responda o número para ligar/desligar:\n"
            + "\n".join(lines) + "\n0. Voltar"]


def _s_notif(phone, text, ctx) -> list[str]:
    if text == "0":
        set_session(phone, "menu", ctx)
        return [_menu(ctx)]
    try:
        key = _NOTIF_KEYS[int(text) - 1][0]
    except (ValueError, IndexError):
        return ["Opção inválida."]
    db.set_setting(key, "false" if config.get_setting_bool(key) else "true")
    return _show_notif(phone, ctx)


# ------------------------------------------------------------ tabela de estados

_STATES = {
    "menu": _s_menu,
    "missions": _s_missions,
    "task_view": _s_task_view,
    "nm_project": _s_nm_project,
    "nm_template": _s_nm_template,
    "nm_title": _s_nm_title,
    "nm_desc": _s_nm_desc,
    "nm_autonomy": _s_nm_autonomy,
    "nm_risk": _s_nm_risk,
    "nm_run": _s_nm_run,
    "ap_list": _s_ap_list,
    "ap_decide": _s_ap_decide,
    "ap_reason": _s_ap_reason,
    "companies": _s_companies,
    "ce_name": _s_ce_name,
    "ce_desc": _s_ce_desc,
    "projects": _s_projects,
    "cp_name": _s_cp_name,
    "notif": _s_notif,
    "sugg_list": _s_sugg_list,
    "sugg_decide": _s_sugg_decide,
}
