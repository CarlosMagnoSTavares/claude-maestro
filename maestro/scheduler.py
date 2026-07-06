"""Agendador: loop in-app (30s) + registro opcional no Agendador de Tarefas do Windows."""
import os
import sys
import datetime
import threading
import subprocess

from . import db, orchestrator, config

_started = False


def start_loop():
    """Inicia a thread do agendador (idempotente)."""
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, daemon=True).start()


def _loop():
    while True:
        try:
            _tick()
        except Exception:
            pass
        try:
            orchestrator.heartbeat_tick()  # retoma runs pausados por limite de uso
        except Exception:
            pass
        threading.Event().wait(30)


def _tick():
    now = datetime.datetime.now()
    today = now.strftime("%Y-%m-%d")
    hhmm = now.strftime("%H:%M")
    weekday = now.weekday()  # 0 = segunda
    rows = db.query("SELECT * FROM schedules WHERE enabled=1")
    for s in rows:
        days = (s["days"] or "1111111").ljust(7, "0")
        if days[weekday] != "1":
            continue
        if s["last_run_date"] == today:
            continue
        if s["time_hhmm"] != hhmm:
            continue
        db.execute("UPDATE schedules SET last_run_date=? WHERE id=?", (today, s["id"]))
        try:
            run_id = orchestrator.start_task(s["task_id"])
            db.add_event(f"Execução agendada disparada (run {run_id}).", run_id=run_id, task_id=s["task_id"])
        except ValueError as e:
            db.add_event(f"Agendamento ignorado: {e}", task_id=s["task_id"], level="warn")


def register_windows_task(schedule_id: int) -> dict:
    """Registra o agendamento no Windows Task Scheduler (roda mesmo com o app fechado)."""
    s = db.query_one("SELECT * FROM schedules WHERE id=?", (schedule_id,))
    if not s:
        return {"ok": False, "output": "Agendamento não encontrado."}
    app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if config.FROZEN:  # app instalado: o próprio executável entende 'run-task'
        cmd_line = f'"{sys.executable}" run-task {s["task_id"]}'
    else:
        cmd_line = f'"{sys.executable}" -m maestro run-task {s["task_id"]}'
    name = f"ClaudeMaestro_Missao{s['task_id']}"
    try:
        proc = subprocess.run(
            ["schtasks", "/create", "/f", "/tn", name, "/tr", cmd_line,
             "/sc", "daily", "/st", s["time_hhmm"]],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace", cwd=app_root,
            creationflags=config.CREATE_NO_WINDOW,
        )
        ok = proc.returncode == 0
        return {"ok": ok, "output": ((proc.stdout or "") + (proc.stderr or "")).strip()[:500]}
    except OSError as e:
        return {"ok": False, "output": str(e)}
