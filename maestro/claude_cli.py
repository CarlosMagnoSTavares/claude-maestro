"""Integração headless com o Claude Code CLI (Windows-safe)."""
import os
import json
import re
import time
import shutil
import datetime
import subprocess

from . import config

# esforço → teto de tokens de raciocínio (MAX_THINKING_TOKENS do Claude Code)
_EFFORT_THINKING = {"low": "1024", "medium": "8192", "high": "31999"}

class LimitReached(Exception):
    """Limite de uso do Claude atingido; `resume_at` diz quando o heartbeat pode retomar."""

    def __init__(self, resume_at: str):
        super().__init__(f"Limite de uso atingido; retomar em {resume_at}")
        self.resume_at = resume_at


_LIMIT_RE = re.compile(
    r"usage limit|rate.?limit|limit reached|limit will reset|out of.*(?:credits|quota)|"
    r"overloaded|529|too many requests|excedeu o limite",
    re.IGNORECASE,
)


def detect_limit(res: dict) -> str | None:
    """Detecta 'limite de uso atingido' numa resposta do CLI.

    Retorna o horário ISO em que vale tentar de novo (heartbeat), ou None se
    não é erro de limite. Fontes do horário, em ordem:
    1. epoch na mensagem (formato 'usage limit reached|1751468400');
    2. texto 'resets at 7pm' / 'reset at 19:00';
    3. fallback: agora + heartbeat_retry_min.
    """
    if res.get("ok"):
        return None
    text = f"{res.get('error','')} {res.get('result','')}"
    if not _LIMIT_RE.search(text):
        return None

    now = datetime.datetime.now()

    m = re.search(r"\|\s*(\d{10,13})", text)
    if m:
        epoch = int(m.group(1))
        if epoch > 10 ** 12:  # milissegundos
            epoch //= 1000
        dt = datetime.datetime.fromtimestamp(epoch)
        if dt > now:
            return (dt + datetime.timedelta(minutes=1)).isoformat(timespec="seconds")

    m = re.search(r"resets?\s+at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text, re.IGNORECASE)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        ampm = (m.group(3) or "").lower()
        if ampm == "pm" and hour < 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
        dt = now.replace(hour=hour % 24, minute=minute, second=0, microsecond=0)
        if dt <= now:
            dt += datetime.timedelta(days=1)
        return (dt + datetime.timedelta(minutes=1)).isoformat(timespec="seconds")

    retry = config.get_setting_int("heartbeat_retry_min") or 15
    return (now + datetime.timedelta(minutes=retry)).isoformat(timespec="seconds")


def find_claude() -> str | None:
    """Localiza o executável do Claude Code no Windows."""
    found = shutil.which("claude")
    if found and os.path.splitext(found)[1].lower() in (".exe", ".cmd", ".bat"):
        return found
    # Candidatos comuns (instalador nativo e npm global)
    home = os.path.expanduser("~")
    candidates = [
        os.path.join(home, ".local", "bin", "claude.exe"),
        os.path.join(home, "AppData", "Roaming", "npm", "claude.cmd"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return found  # pode ser shim sem extensão; tratado em _build_cmd


def _build_cmd(exe: str, args: list[str]) -> list[str]:
    ext = os.path.splitext(exe)[1].lower()
    if ext in (".cmd", ".bat"):
        return ["cmd", "/c", exe] + args
    return [exe] + args


_version_cache: dict = {"exe": None, "version": None, "ts": 0.0}


def claude_version(force: bool = False) -> str | None:
    """Versão do CLI, com cache (o status é consultado a cada 5s pela UI)."""
    exe = find_claude()
    if not exe:
        _version_cache.update(exe=None, version=None, ts=0.0)
        return None
    now = time.time()
    if (not force and _version_cache["exe"] == exe
            and _version_cache["version"] and now - _version_cache["ts"] < 300):
        return _version_cache["version"]
    try:
        out = subprocess.run(
            _build_cmd(exe, ["--version"]),
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace",
            creationflags=config.CREATE_NO_WINDOW,
        )
        if out.returncode == 0:
            version = out.stdout.strip().splitlines()[0]
            _version_cache.update(exe=exe, version=version, ts=now)
            return version
    except (OSError, subprocess.TimeoutExpired):
        pass
    return _version_cache["version"] if _version_cache["exe"] == exe else None


def _parse_messages(stdout: str) -> list[dict]:
    """Aceita stream-json (NDJSON), JSON único ou array de mensagens."""
    if not stdout:
        return []
    try:
        data = json.loads(stdout)
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict)]
        return [data] if isinstance(data, dict) else []
    except json.JSONDecodeError:
        pass
    msgs = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line or line[0] not in "[{":
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, list):
            msgs.extend(x for x in obj if isinstance(x, dict))
        elif isinstance(obj, dict):
            msgs.append(obj)
    return msgs


def _extract_thinking(messages: list[dict]) -> str:
    """Concatena os blocos de raciocínio (thinking) das mensagens do assistente."""
    parts = []
    for msg in messages:
        if msg.get("type") != "assistant":
            continue
        content = (msg.get("message") or {}).get("content") or []
        if not isinstance(content, list):
            continue
        for item in content:
            if isinstance(item, dict) and item.get("type") == "thinking" and item.get("thinking"):
                parts.append(str(item["thinking"]))
    return "\n\n---\n\n".join(parts)


def run_claude(
    prompt: str,
    cwd: str,
    model: str | None = None,
    system_prompt: str | None = None,
    yolo: bool = True,
    max_turns: int = 30,
    timeout_min: int = 20,
    effort: str | None = None,
    log_ctx: dict | None = None,
) -> dict:
    """Executa `claude -p` headless e retorna dict padronizado.

    Retorno: {ok, result, cost_usd, error, raw, thinking}
    O prompt vai via stdin para evitar limites/quoting de argv no Windows.
    Toda chamada é registrada em llm_calls (prompt RAW, thinking, saída completa)
    quando `log_ctx` é informado — transparência total para o painel de Debug.
    """
    exe = find_claude()
    if not exe:
        res = {"ok": False, "result": "", "cost_usd": 0.0,
               "error": "Claude Code CLI não encontrado.", "raw": None, "thinking": ""}
        _log_call(log_ctx, prompt, system_prompt, model, effort, [], "", "", res, 0)
        return res

    # stream-json + --verbose: captura TODAS as mensagens (inclusive thinking)
    args = ["-p", "--output-format", "stream-json", "--verbose",
            "--max-turns", str(max_turns)]
    if model:
        args += ["--model", model]
    if system_prompt:
        args += ["--append-system-prompt", system_prompt]
    if yolo:
        args += ["--dangerously-skip-permissions"]
    else:
        args += ["--permission-mode", "acceptEdits"]

    env = os.environ.copy()
    env.setdefault("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1")
    if effort in _EFFORT_THINKING:
        env["MAX_THINKING_TOKENS"] = _EFFORT_THINKING[effort]

    started = time.monotonic()
    try:
        proc = subprocess.run(
            _build_cmd(exe, args),
            input=prompt,
            capture_output=True, text=True,
            cwd=cwd or None,
            timeout=timeout_min * 60,
            encoding="utf-8", errors="replace",
            env=env,
            creationflags=config.CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        res = {"ok": False, "result": "", "cost_usd": 0.0,
               "error": f"Tempo esgotado ({timeout_min} min).", "raw": None, "thinking": ""}
        _log_call(log_ctx, prompt, system_prompt, model, effort, args, "", "", res,
                  time.monotonic() - started)
        return res
    except OSError as e:
        res = {"ok": False, "result": "", "cost_usd": 0.0,
               "error": f"Falha ao executar o Claude Code: {e}", "raw": None, "thinking": ""}
        _log_call(log_ctx, prompt, system_prompt, model, effort, args, "", "", res,
                  time.monotonic() - started)
        return res

    duration = time.monotonic() - started
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    messages = _parse_messages(stdout)
    thinking = _extract_thinking(messages)
    results = [m for m in messages if m.get("type") == "result" or
               ("result" in m and "total_cost_usd" in m)]
    data = results[-1] if results else None

    if isinstance(data, dict):
        result_text = data.get("result") or ""
        cost = float(data.get("total_cost_usd") or 0.0)
        is_err = bool(data.get("is_error")) or proc.returncode != 0
        err = "" if not is_err else (result_text or stderr[:2000] or "Erro na execução.")
        res = {"ok": not is_err, "result": result_text, "cost_usd": cost,
               "error": err, "raw": data, "thinking": thinking}
    else:
        err = stderr[:2000] or f"Saída inválida do Claude Code (código {proc.returncode})."
        res = {"ok": False, "result": stdout[:2000], "cost_usd": 0.0,
               "error": err, "raw": None, "thinking": thinking}

    _log_call(log_ctx, prompt, system_prompt, model, effort, args, stdout, stderr,
              res, duration)
    return res


def _log_call(log_ctx, prompt, system_prompt, model, effort, args, stdout, stderr,
              res, duration_s: float):
    """Grava a chamada em llm_calls (best-effort: debug nunca derruba a execução)."""
    if not log_ctx:
        return
    try:
        from . import db
        db.add_llm_call(
            log_ctx, model=model or "", effort=effort or "",
            prompt=prompt, system_prompt=system_prompt or "",
            args=" ".join(args or []), stdout=stdout, stderr=stderr,
            thinking=res.get("thinking", ""), result=res.get("result", ""),
            ok=res.get("ok", False), error=res.get("error", ""),
            cost_usd=res.get("cost_usd", 0.0), duration_ms=int(duration_s * 1000),
        )
    except Exception:
        pass


def extract_json(text: str):
    """Extrai o primeiro objeto/array JSON de um texto de modelo (tolerante a cercas)."""
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        while start != -1:
            depth = 0
            in_str = False
            esc = False
            for i in range(start, len(text)):
                ch = text[i]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                    continue
                if ch == '"':
                    in_str = True
                elif ch == opener:
                    depth += 1
                elif ch == closer:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start:i + 1])
                        except json.JSONDecodeError:
                            break
            start = text.find(opener, start + 1)
    return None
