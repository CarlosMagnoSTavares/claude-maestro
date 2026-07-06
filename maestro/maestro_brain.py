"""Cérebro do Maestro: recruta agentes, atribui skills e planeja micro-tarefas.

Todas as decisões são tomadas pelo próprio Claude Code em modo headless —
o usuário nunca é consultado.
"""
import os
import json
import re

from . import db, config, claude_cli, skills_catalog


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9-]+", "-", name.lower().strip()).strip("-")
    return s or "agente"


def _brain_call(prompt: str, cwd: str, run_id: int | None = None,
                kind: str = "") -> dict:
    res = claude_cli.run_claude(
        prompt,
        cwd=cwd,
        model=config.get_setting("brain_model"),
        yolo=config.get_setting_bool("yolo"),
        max_turns=config.get_setting_int("max_turns_brain"),
        timeout_min=10,
        log_ctx={"run_id": run_id, "kind": kind, "agent_name": "maestro"},
    )
    limit_at = claude_cli.detect_limit(res)
    if limit_at:  # limite de uso → o orquestrador pausa e o heartbeat retoma
        raise claude_cli.LimitReached(limit_at)
    return res


def _company_context(company: dict, project: dict) -> str:
    return (
        f"EMPRESA: {company['name']}\n"
        f"Objetivos: {company.get('objectives') or '-'}\n"
        f"Impedimentos: {company.get('impediments') or '-'}\n"
        f"Problemas a resolver: {company.get('problems') or '-'}\n"
        f"PROJETO: {project['name']} — {project.get('description') or '-'}\n"
    )


# ---------------------------------------------------------------- recrutar

def recruit_agents(company: dict, project: dict, mission: str,
                   run_id: int | None = None) -> dict:
    """Decide quais agentes contratar (criar) ou reaproveitar, e as skills de cada um.

    Retorna {"agents": [{name, role, description, system_prompt, model, effort,
    skills:[{owner_repo, skill}], reused}], "cost": x}
    """
    # agentes do workspace desta empresa (ou antigos sem empresa definida)
    existing = db.query(
        "SELECT name, role, description FROM agents "
        "WHERE company_id IS NULL OR company_id=? ORDER BY name", (company["id"],))
    catalog = skills_catalog.top_skills(30)
    # descoberta automática: skills relevantes para ESTA missão (busca no skills.sh)
    seen = {(s["owner_repo"], s["skill"]) for s in catalog}
    for s in skills_catalog.search_for_mission(mission):
        if (s["owner_repo"], s["skill"]) not in seen:
            catalog.append(s)
    catalog_lines = "\n".join(
        f"- {s['skill']} (repo: {s['owner_repo']}, installs: {s['installs']})"
        + (f" — {s['description']}" if s.get("description") else "")
        for s in catalog
    ) or "- (catálogo indisponível)"
    existing_lines = "\n".join(
        f"- {a['name']}: {a['role']} — {a['description']}" for a in existing
    ) or "- (nenhum agente existente)"
    max_agents = config.get_setting_int("max_agents")

    prompt = f"""Você é o MAESTRO, um orquestrador de agentes de IA. Analise a missão e monte a melhor equipe.

{_company_context(company, project)}
MISSÃO: {mission}

AGENTES JÁ EXISTENTES (reaproveite quando fizer sentido):
{existing_lines}

CATÁLOGO DE SKILLS DISPONÍVEIS (ordenado por popularidade; escolha SOMENTE destas):
{catalog_lines}

Pergunte-se: "Quem eu preciso contratar (criar como agente local) para executar esta missão da melhor forma possível, ou já tenho esses agentes?"

Responda APENAS com JSON válido, sem nenhum texto fora do JSON, no formato:
{{
  "agents": [
    {{
      "name": "slug-do-agente",
      "reused": false,
      "role": "papel curto",
      "description": "quando usar este agente (1 frase)",
      "system_prompt": "prompt de sistema completo do agente em português: persona, especialidade, padrões de qualidade, como trabalhar",
      "model": "haiku|sonnet|opus",
      "effort": "low|medium|high",
      "reports_to": "slug-do-lider ou vazio se reporta direto ao Maestro",
      "skills": [{{"owner_repo": "owner/repo", "skill": "nome-da-skill"}}]
    }}
  ]
}}

Regras:
- No máximo {max_agents} agentes. Prefira menos agentes, mais especializados.
- "reused": true para agentes da lista de existentes (mantenha o mesmo name; system_prompt pode ser vazio).
- "reports_to": monte uma CADEIA HIERÁRQUICA como numa empresa (ex.: dev reporta ao tech-lead,
  tech-lead reporta ao Maestro com reports_to vazio). Use apenas slugs desta equipe.
- "model": escolha o modelo MAIS BARATO que dá conta da tarefa do agente
  (haiku = simples/rápido, sonnet = padrão, opus = só para trabalho muito complexo).
- "effort": esforço de raciocínio do agente (low = mecânico, medium = padrão, high = problemas difíceis).
  Combine com o modelo: evite opus+high salvo necessidade real — o gestor pode vetar combinações caras.
- SEMPRE atribua as skills necessárias para o trabalho de cada agente: 1 a 3 skills por agente,
  priorizando as do catálogo acima (mais populares primeiro). Se nenhuma do catálogo servir,
  você pode sugerir o nome de outra skill que conheça do skills.sh — ela será validada e só
  entra se existir com boa reputação. Só deixe sem skill se realmente nada ajudar.
- NÃO execute nada, NÃO crie arquivos: apenas responda o JSON."""

    res = _brain_call(prompt, cwd=project["folder"], run_id=run_id, kind="recrutar")
    if not res["ok"]:
        raise RuntimeError(f"Falha ao recrutar agentes: {res['error']}")
    data = claude_cli.extract_json(res["result"]) or {}
    agents = data.get("agents") or []
    if not agents:
        raise RuntimeError("O Maestro não retornou agentes válidos.")

    valid = []
    for a in agents[:max_agents]:
        name = _slug(str(a.get("name") or ""))
        skills = []
        for sk in (a.get("skills") or [])[:3]:
            skill_name = str(sk.get("skill") or "").strip()
            found = skills_catalog.find_skill(skill_name)
            if found:  # filtra skills inventadas/baixa reputação
                skills.append({"owner_repo": found["owner_repo"], "skill": found["skill"]})
        valid.append({
            "name": name,
            "reused": bool(a.get("reused")),
            "role": str(a.get("role") or "")[:200],
            "description": str(a.get("description") or "")[:500],
            "system_prompt": str(a.get("system_prompt") or ""),
            # escolha da IA limitada pelos tetos configurados (max_model/max_effort)
            "model": config.clamp_model(str(a.get("model") or "")),
            "effort": config.clamp_effort(str(a.get("effort") or "")),
            "reports_to": _slug(str(a.get("reports_to") or "")) if a.get("reports_to") else "",
            "skills": skills,
        })
    names = {v["name"] for v in valid}
    for v in valid:  # hierarquia só pode apontar para membros da equipe
        if v["reports_to"] not in names or v["reports_to"] == v["name"]:
            v["reports_to"] = ""
    return {"agents": valid, "cost": res["cost_usd"]}


def persist_agents(agents: list[dict], project_folder: str,
                   company_id: int | None = None) -> list[dict]:
    """Grava agentes no banco e em .claude/agents/*.md (formato nativo do Claude Code)."""
    agents_dir = os.path.join(project_folder, ".claude", "agents")
    os.makedirs(agents_dir, exist_ok=True)
    final = []
    for a in agents:
        row = db.query_one("SELECT * FROM agents WHERE name = ?", (a["name"],))
        if row and a["reused"]:
            merged = {**row, "skills": json.loads(row["skills_json"] or "[]")}
            new_skills = merged["skills"] + [s for s in a["skills"] if s not in merged["skills"]]
            db.execute("UPDATE agents SET skills_json = ?, reports_to = ?, "
                       "company_id = COALESCE(company_id, ?) WHERE id = ?",
                       (json.dumps(new_skills, ensure_ascii=False),
                        a.get("reports_to") or row.get("reports_to") or "", company_id, row["id"]))
            merged["reports_to"] = a.get("reports_to") or row.get("reports_to") or ""
            merged["skills"] = new_skills
            final.append(merged)
        else:
            sp = a["system_prompt"] or f"Você é {a['name']}, {a['role']}. {a['description']}"
            if row:
                db.execute(
                    "UPDATE agents SET role=?, description=?, system_prompt=?, skills_json=?, "
                    "model=?, effort=?, reports_to=?, company_id=COALESCE(company_id, ?) WHERE id=?",
                    (a["role"], a["description"], sp,
                     json.dumps(a["skills"], ensure_ascii=False),
                     a.get("model") or "", a.get("effort") or "",
                     a.get("reports_to") or "", company_id, row["id"]),
                )
            else:
                db.execute(
                    "INSERT INTO agents(name, role, description, system_prompt, skills_json, "
                    "model, effort, reports_to, company_id, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (a["name"], a["role"], a["description"], sp,
                     json.dumps(a["skills"], ensure_ascii=False),
                     a.get("model") or "", a.get("effort") or "",
                     a.get("reports_to") or "", company_id, db.now()),
                )
            final.append({**a, "system_prompt": sp})

        # arquivo nativo .claude/agents/<name>.md (portabilidade/exportação)
        sp = final[-1].get("system_prompt") or ""
        desc = (final[-1].get("description") or "").replace("\n", " ")
        md = f"---\nname: {a['name']}\ndescription: {desc}\n---\n\n{sp}\n"
        with open(os.path.join(agents_dir, f"{a['name']}.md"), "w", encoding="utf-8") as f:
            f.write(md)
    return final


# ---------------------------------------------------------------- retrospectiva

def suggest_next_missions(company: dict, project: dict, task: dict,
                          run_id: int, summary: str, files: list[str]) -> list[dict]:
    """Proatividade da equipe: ao concluir uma missão, olha os objetivos da empresa
    e sugere as próximas missões de maior impacto. Grava na tabela suggestions."""
    files_txt = "\n".join(f"- {f}" for f in files[:20]) or "- (nenhum arquivo listado)"
    open_titles = [t["title"] for t in db.query(
        "SELECT title FROM tasks WHERE project_id IN "
        "(SELECT id FROM projects WHERE company_id=?) AND status != 'concluida' "
        "ORDER BY id DESC LIMIT 15", (company["id"],))]
    pend = [s["title"] for s in db.query(
        "SELECT title FROM suggestions WHERE company_id=? AND status='pendente'",
        (company["id"],))]
    existing = "\n".join(f"- {t}" for t in open_titles + pend) or "- (nada em aberto)"

    prompt = f"""Você é o MAESTRO em modo RETROSPECTIVA. Uma missão acabou de ser concluída.
Pense como um sócio proativo: o que a empresa deveria fazer EM SEGUIDA para avançar nos objetivos?

{_company_context(company, project)}
MISSÃO CONCLUÍDA: {task['title']}
O que foi pedido: {task['mission'][:1500]}
Resultado: {summary[:800]}
Arquivos entregues:
{files_txt}

MISSÕES JÁ ABERTAS OU JÁ SUGERIDAS (NÃO repita nem proponha variações destas):
{existing}

Responda APENAS com JSON válido:
{{
  "suggestions": [
    {{
      "title": "nome curto da missão sugerida",
      "mission": "instrução completa do que deve ser feito (autossuficiente, em português)",
      "reason": "1 frase: por que isso ajuda os objetivos da empresa agora"
    }}
  ]
}}

Regras:
- 0 a 3 sugestões. Qualidade sobre quantidade: se não houver nada de impacto real, retorne lista vazia.
- Sugira melhorias sobre o que foi entregue, próximos passos naturais, ou ataques diretos
  aos objetivos/problemas da empresa. Nada genérico.
- NÃO execute nada: apenas responda o JSON."""

    res = _brain_call(prompt, cwd=project["folder"], run_id=run_id, kind="retrospectiva")
    if not res["ok"]:
        return []
    data = claude_cli.extract_json(res["result"]) or {}
    out = []
    for s in (data.get("suggestions") or [])[:3]:
        title = str(s.get("title") or "").strip()[:200]
        mission = str(s.get("mission") or "").strip()[:4000]
        if not (title and mission):
            continue
        sid = db.execute(
            "INSERT INTO suggestions(company_id, project_id, task_id, run_id, title, "
            "mission, reason, status, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (company["id"], project["id"], task["id"], run_id, title, mission,
             str(s.get("reason") or "").strip()[:500], "pendente", db.now()),
        )
        out.append({"id": sid, "title": title, "reason": s.get("reason") or ""})
    if out:
        db.add_event(f"Retrospectiva: equipe sugeriu {len(out)} próxima(s) missão(ões).",
                     run_id=run_id, task_id=task["id"])
    return out


# ---------------------------------------------------------------- planejar

def plan_mission(company: dict, project: dict, mission: str, agents: list[dict],
                 run_id: int | None = None, feedback: str = "") -> dict:
    """Quebra a missão em micro-tarefas ordenadas, com dependências e paralelismo.

    Retorna {"microtasks": [{description, agent, priority, parallel, depends_on:[índices 1-based]}], "cost": x}
    """
    team = "\n".join(f"- {a['name']}: {a['role']}" for a in agents)
    prompt = f"""Você é o MAESTRO em modo PLANEJAMENTO. Quebre a missão em micro-tarefas executáveis.

{_company_context(company, project)}
MISSÃO: {mission}

EQUIPE DISPONÍVEL:
{team}
{("FEEDBACK DO GESTOR (o plano anterior foi REPROVADO por este motivo — corrija o plano levando isto em conta):" + chr(10) + feedback + chr(10)) if feedback else ""}
Responda APENAS com JSON válido, sem texto fora do JSON:
{{
  "microtasks": [
    {{
      "id": 1,
      "description": "instrução completa e autossuficiente da micro-tarefa (o agente só verá isto)",
      "agent": "slug-do-agente-responsavel",
      "priority": 1,
      "parallel": true,
      "depends_on": []
    }}
  ]
}}

Regras:
- 2 a 10 micro-tarefas, ordenadas por prioridade (1 = mais prioritária).
- "depends_on": lista de ids de micro-tarefas que precisam terminar antes.
- "parallel": true se pode rodar junto com outras micro-tarefas prontas; false se precisa rodar sozinha.
- Cada micro-tarefa deve ser concreta e verificável (arquivos a criar, o que deve funcionar).
- "agent" deve ser exatamente um dos slugs da equipe.
- NÃO execute nada: apenas responda o JSON."""

    res = _brain_call(prompt, cwd=project["folder"], run_id=run_id, kind="planejar")
    if not res["ok"]:
        raise RuntimeError(f"Falha no planejamento: {res['error']}")
    data = claude_cli.extract_json(res["result"]) or {}
    raw = data.get("microtasks") or []
    if not raw:
        raise RuntimeError("O Maestro não retornou um plano válido.")

    agent_names = {a["name"] for a in agents}
    default_agent = agents[0]["name"] if agents else ""
    id_map, cleaned = {}, []
    for i, m in enumerate(raw[:10], start=1):
        id_map[m.get("id", i)] = i
        cleaned.append({
            "ord": i,
            "description": str(m.get("description") or "").strip(),
            "agent": m.get("agent") if m.get("agent") in agent_names else default_agent,
            "priority": int(m.get("priority") or i),
            "parallel": bool(m.get("parallel", True)),
            "depends_on": m.get("depends_on") or [],
        })
    for m in cleaned:  # remapeia deps para ords válidos, remove auto-dependência
        m["depends_on"] = sorted({
            id_map[d] for d in m["depends_on"]
            if d in id_map and id_map[d] != m["ord"]
        })
    cleaned = [m for m in cleaned if m["description"]]
    if not cleaned:
        raise RuntimeError("Plano vazio após validação.")
    return {"microtasks": cleaned, "cost": res["cost_usd"]}
