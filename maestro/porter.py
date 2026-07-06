"""Exportação/Importação total: Pacote Maestro v1 (.zip).

Formato aberto: manifest.json + data.json (entidades) + projects/<id>/** (artefatos,
incluindo .claude/agents e .claude/skills no formato nativo do Claude Code).
"""
import os
import json
import zipfile
import datetime

from . import db, config

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".next", "dist"}
MAX_FILE_MB = 50


def export_package(include_artifacts: bool = True) -> str:
    """Gera o .zip em data/exports e retorna o caminho."""
    config.ensure_dirs()
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(config.EXPORTS_DIR, f"maestro_export_{stamp}.zip")
    data = db.export_all()
    manifest = {
        "format": "maestro-package",
        "version": 1,
        "app_version": config.APP_VERSION,
        "exported_at": db.now(),
        "includes_artifacts": include_artifacts,
        "standards": {
            "agents": ".claude/agents/*.md (formato nativo Claude Code)",
            "skills": ".claude/skills/** (instaladas via skills.sh)",
        },
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        z.writestr("data.json", json.dumps(data, ensure_ascii=False, indent=2))
        if include_artifacts:
            for p in data["projects"]:
                folder = p["folder"]
                if not os.path.isdir(folder):
                    continue
                base = f"projects/{p['id']}/"
                for root, dirs, files in os.walk(folder):
                    dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
                    for fn in files:
                        full = os.path.join(root, fn)
                        try:
                            if os.path.getsize(full) > MAX_FILE_MB * 1024 * 1024:
                                continue
                            rel = os.path.relpath(full, folder).replace("\\", "/")
                            z.write(full, base + rel)
                        except OSError:
                            continue
    return path


def import_package(zip_path: str, restore_base: str | None = None) -> dict:
    """Importa um Pacote Maestro: entidades (ids remapeados) + artefatos.

    Pastas de projeto ausentes são restauradas em restore_base
    (padrão: Desktop/MaestroProjetos/<nome>).
    """
    if restore_base is None:
        restore_base = os.path.join(os.path.expanduser("~"), "Desktop", "MaestroProjetos")

    with zipfile.ZipFile(zip_path) as z:
        names = set(z.namelist())
        if "manifest.json" not in names or "data.json" not in names:
            raise ValueError("Arquivo não é um Pacote Maestro válido.")
        manifest = json.loads(z.read("manifest.json"))
        if manifest.get("format") != "maestro-package":
            raise ValueError("Formato de pacote desconhecido.")
        data = json.loads(z.read("data.json"))

        counts = {"companies": 0, "projects": 0, "tasks": 0, "agents": 0, "schedules": 0}

        # empresas: mescla por nome
        company_map = {}
        for c in data.get("companies", []):
            row = db.query_one("SELECT id FROM companies WHERE name=?", (c["name"],))
            if row:
                company_map[c["id"]] = row["id"]
            else:
                new_id = db.execute(
                    "INSERT INTO companies(name, objectives, impediments, problems, extra_json, created_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (c["name"], c.get("objectives", ""), c.get("impediments", ""),
                     c.get("problems", ""), c.get("extra_json", ""), db.now()),
                )
                company_map[c["id"]] = new_id
                counts["companies"] += 1

        # agentes: mescla por nome (único)
        for a in data.get("agents", []):
            if db.query_one("SELECT id FROM agents WHERE name=?", (a["name"],)):
                continue
            db.execute(
                "INSERT INTO agents(name, role, description, system_prompt, skills_json, created_at) "
                "VALUES(?,?,?,?,?,?)",
                (a["name"], a.get("role", ""), a.get("description", ""),
                 a.get("system_prompt", ""), a.get("skills_json", "[]"), db.now()),
            )
            counts["agents"] += 1

        # projetos: sempre insere; restaura artefatos se a pasta não existir
        project_map = {}
        for p in data.get("projects", []):
            folder = p["folder"]
            prefix = f"projects/{p['id']}/"
            has_artifacts = any(n.startswith(prefix) for n in names)
            if not os.path.isdir(folder):
                folder = os.path.join(restore_base, p["name"])
                os.makedirs(folder, exist_ok=True)
            if has_artifacts:
                for n in names:
                    if not n.startswith(prefix) or n.endswith("/"):
                        continue
                    rel = n[len(prefix):]
                    dest = os.path.join(folder, *rel.split("/"))
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    if not os.path.exists(dest):
                        with open(dest, "wb") as f:
                            f.write(z.read(n))
            new_id = db.execute(
                "INSERT INTO projects(company_id, name, folder, description, created_at) VALUES(?,?,?,?,?)",
                (company_map.get(p["company_id"], list(company_map.values())[0] if company_map else 1),
                 p["name"], folder, p.get("description", ""), db.now()),
            )
            project_map[p["id"]] = new_id
            counts["projects"] += 1

        # missões + histórico + agendamentos
        task_map = {}
        for t in data.get("tasks", []):
            if t["project_id"] not in project_map:
                continue
            new_id = db.execute(
                "INSERT INTO tasks(project_id, title, mission, mode, status, created_at) VALUES(?,?,?,?,?,?)",
                (project_map[t["project_id"]], t["title"], t["mission"],
                 t.get("mode", "yolo"),
                 t["status"] if t.get("status") in ("concluida", "falhou") else "nova",
                 db.now()),
            )
            task_map[t["id"]] = new_id
            counts["tasks"] += 1

        run_map = {}
        for r in data.get("runs", []):
            if r["task_id"] not in task_map:
                continue
            run_map[r["id"]] = db.execute(
                "INSERT INTO runs(task_id, status, phase, summary, total_cost_usd, error, started_at, finished_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (task_map[r["task_id"]], r.get("status", ""), r.get("phase", ""),
                 r.get("summary", ""), r.get("total_cost_usd", 0), r.get("error", ""),
                 r.get("started_at"), r.get("finished_at")),
            )
        for m in data.get("microtasks", []):
            if m["run_id"] not in run_map:
                continue
            db.execute(
                "INSERT INTO microtasks(run_id, ord, description, agent_name, priority, parallel, "
                "depends_on, status, output, cost_usd, started_at, finished_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_map[m["run_id"]], m.get("ord", 0), m.get("description", ""),
                 m.get("agent_name", ""), m.get("priority", 1), m.get("parallel", 1),
                 m.get("depends_on", "[]"), m.get("status", ""), m.get("output", ""),
                 m.get("cost_usd", 0), m.get("started_at"), m.get("finished_at")),
            )
        for s in data.get("schedules", []):
            if s["task_id"] not in task_map:
                continue
            db.execute(
                "INSERT INTO schedules(task_id, time_hhmm, days, enabled, last_run_date) VALUES(?,?,?,?,?)",
                (task_map[s["task_id"]], s["time_hhmm"], s.get("days", "1111111"),
                 s.get("enabled", 1), ""),
            )
            counts["schedules"] += 1

    return counts
