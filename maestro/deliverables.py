"""Entregas de uma execução — responde "cadê o resultado?".

Varre a pasta do projeto e lista os arquivos criados/alterados desde o início
do run (excluindo arquivos internos), para o gestor ver e abrir com um clique.
"""
import os
import datetime

from . import db

_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".claude", ".venv", "venv"}
_SKIP_FILES = {"CLAUDE.md", "MEMORY.md"}
_MAX_FILES = 200


def _run_folder(run_id: int):
    r = db.query_one(
        "SELECT r.started_at, p.folder FROM runs r "
        "JOIN tasks t ON t.id=r.task_id JOIN projects p ON p.id=t.project_id "
        "WHERE r.id=?", (run_id,))
    return r


def for_run(run_id: int) -> dict | None:
    r = _run_folder(run_id)
    if not r:
        return None
    folder = r["folder"]
    out = {"folder": folder, "files": []}
    if not (folder and os.path.isdir(folder)):
        return out
    try:
        since = datetime.datetime.fromisoformat(r["started_at"]).timestamp()
    except (TypeError, ValueError):
        return out
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
        for name in files:
            if name in _SKIP_FILES:
                continue
            full = os.path.join(root, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            if st.st_mtime < since:
                continue
            rel = os.path.relpath(full, folder)
            out["files"].append({
                "name": name, "path": rel.replace("\\", "/"),
                "size": st.st_size,
                "modified": datetime.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
            })
            if len(out["files"]) >= _MAX_FILES:
                out["truncated"] = True
                return out
    out["files"].sort(key=lambda f: f["modified"], reverse=True)
    return out


def safe_path(run_id: int, rel: str) -> str | None:
    """Resolve o path relativo dentro da pasta do projeto (bloqueia traversal)."""
    r = _run_folder(run_id)
    if not (r and r["folder"]):
        return None
    base = os.path.realpath(r["folder"])
    full = os.path.realpath(os.path.join(base, rel))
    if full == base or not full.startswith(base + os.sep):
        return None
    return full if os.path.isfile(full) else None


def summary_lines(run_id: int, limit: int = 5) -> list[str]:
    """Top arquivos entregues (para o push do WhatsApp)."""
    d = for_run(run_id) or {}
    files = (d.get("files") or [])[:limit]
    lines = [f"📄 {f['path']}" for f in files]
    rest = len(d.get("files") or []) - len(files)
    if rest > 0:
        lines.append(f"… e mais {rest} arquivo(s) na pasta do projeto.")
    return lines
