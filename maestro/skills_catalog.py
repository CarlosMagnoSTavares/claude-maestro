"""Catálogo de skills do skills.sh: leaderboard por popularidade + instalação via npx.

Estratégia:
1. Busca o HTML do leaderboard (server-rendered) e extrai owner/repo/skill + installs (8 semanas).
2. Cache em SQLite por 24h.
3. Fallback curado (skills oficiais/consagradas) se a rede falhar.
4. Filtro de reputação: descarta skills abaixo de `min_installs`.
"""
import re
import json
import shutil
import subprocess
import datetime
import urllib.request
import urllib.parse

from . import db, config

SKILLS_URL = "https://skills.sh/"
SEARCH_URL = "https://skills.sh/api/search?q="
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ClaudeMaestro/1.0"

# Fallback curado: skills conhecidas e de alta reputação (usado offline)
CURATED = [
    {"owner_repo": "vercel-labs/skills", "skill": "find-skills", "installs": 500000,
     "description": "Descobre e instala outras skills sob demanda"},
    {"owner_repo": "anthropics/skills", "skill": "frontend-design", "installs": 300000,
     "description": "Design de interfaces web bonitas e funcionais"},
    {"owner_repo": "anthropics/skills", "skill": "docx", "installs": 150000,
     "description": "Criar e editar documentos Word (.docx)"},
    {"owner_repo": "anthropics/skills", "skill": "xlsx", "installs": 150000,
     "description": "Criar e editar planilhas Excel (.xlsx)"},
    {"owner_repo": "anthropics/skills", "skill": "pptx", "installs": 140000,
     "description": "Criar e editar apresentações PowerPoint (.pptx)"},
    {"owner_repo": "anthropics/skills", "skill": "pdf", "installs": 140000,
     "description": "Ler, criar e manipular arquivos PDF"},
    {"owner_repo": "anthropics/skills", "skill": "webapp-testing", "installs": 90000,
     "description": "Testar aplicações web com navegador automatizado"},
    {"owner_repo": "anthropics/skills", "skill": "mcp-builder", "installs": 60000,
     "description": "Construir servidores MCP"},
    {"owner_repo": "anthropics/skills", "skill": "artifacts-builder", "installs": 50000,
     "description": "Construir artefatos web interativos"},
    {"owner_repo": "anthropics/skills", "skill": "skill-creator", "installs": 45000,
     "description": "Criar novas skills personalizadas"},
]

_ROW_RE = re.compile(
    r'href="/([\w.-]+)/([\w.-]+)/([\w.-]+)"'      # /owner/repo/skill
    r'(?:(?!href="/).)*?'                          # dentro da mesma linha do ranking
    r'aria-label="Weekly installs: ([\d,\s]+)"',
    re.DOTALL,
)


def _fetch_leaderboard() -> list[dict]:
    req = urllib.request.Request(SKILLS_URL, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=20) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    seen = {}
    for m in _ROW_RE.finditer(html):
        owner, repo, skill, weekly = m.groups()
        if owner in ("agent", "topic", "official", "audits", "docs", "site") or "." in repo:
            continue
        installs = sum(int(n.replace(",", "")) for n in re.findall(r"[\d,]+", weekly))
        key = (f"{owner}/{repo}", skill)
        if key not in seen or installs > seen[key]:
            seen[key] = installs
    return [
        {"owner_repo": k[0], "skill": k[1], "installs": v, "description": ""}
        for k, v in seen.items()
    ]


def refresh_catalog(force: bool = False) -> dict:
    """Atualiza o cache do catálogo (24h). Retorna {source, count}."""
    newest = db.query_one("SELECT MAX(fetched_at) AS f FROM skills_cache")
    if not force and newest and newest["f"]:
        age = datetime.datetime.now() - datetime.datetime.fromisoformat(newest["f"])
        if age.total_seconds() < 24 * 3600:
            n = db.query_one("SELECT COUNT(*) AS n FROM skills_cache")["n"]
            return {"source": "cache", "count": n}

    rows, source = [], "skills.sh"
    try:
        rows = _fetch_leaderboard()
    except Exception:
        rows = []
    if not rows:
        rows, source = CURATED, "curado (offline)"

    ts = db.now()
    db.executemany(
        "INSERT INTO skills_cache(owner_repo, skill, installs, description, fetched_at) "
        "VALUES(?,?,?,?,?) "
        "ON CONFLICT(owner_repo, skill) DO UPDATE SET installs=excluded.installs, "
        "fetched_at=excluded.fetched_at",
        [(r["owner_repo"], r["skill"], r["installs"], r.get("description", ""), ts) for r in rows],
    )
    return {"source": source, "count": len(rows)}


def top_skills(limit: int = 30) -> list[dict]:
    """Skills mais populares acima do limiar de reputação."""
    refresh_catalog()
    min_installs = config.get_setting_int("min_installs")
    return db.query(
        "SELECT owner_repo, skill, installs, description FROM skills_cache "
        "WHERE installs >= ? ORDER BY installs DESC LIMIT ?",
        (min_installs, limit),
    )


def search_skills(query: str, limit: int = 6) -> list[dict]:
    """Busca fuzzy no skills.sh (API oficial) e alimenta o cache local."""
    query = (query or "").strip()
    if not query:
        return []
    try:
        req = urllib.request.Request(SEARCH_URL + urllib.parse.quote(query),
                                     headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return []
    min_installs = config.get_setting_int("min_installs")
    rows = []
    for s in (data.get("skills") or [])[:limit * 2]:
        owner_repo = str(s.get("source") or "")
        skill = str(s.get("skillId") or s.get("name") or "")
        installs = int(s.get("installs") or 0)
        if not (owner_repo and skill) or installs < min_installs:
            continue
        rows.append({"owner_repo": owner_repo, "skill": skill,
                     "installs": installs, "description": ""})
        if len(rows) >= limit:
            break
    if rows:
        ts = db.now()
        db.executemany(
            "INSERT INTO skills_cache(owner_repo, skill, installs, description, fetched_at) "
            "VALUES(?,?,?,?,?) ON CONFLICT(owner_repo, skill) DO UPDATE SET "
            "installs=excluded.installs, fetched_at=excluded.fetched_at",
            [(r["owner_repo"], r["skill"], r["installs"], r["description"], ts) for r in rows],
        )
    return rows


_STOPWORDS = {
    "para", "com", "uma", "que", "das", "dos", "meu", "minha", "site", "criar",
    "fazer", "gerar", "sobre", "empresa", "projeto", "arquivo", "arquivos",
    "pasta", "portugues", "português", "brasil", "cada", "todos", "todas",
    "quando", "como", "onde", "mais", "menos", "the", "and", "for", "with",
}


def search_for_mission(mission_text: str, max_terms: int = 5,
                       limit: int = 12) -> list[dict]:
    """Descobre skills relevantes para a missão: extrai palavras-chave e busca no skills.sh."""
    words = re.findall(r"[a-zA-ZÀ-ÿ][\wÀ-ÿ-]{3,}", (mission_text or "").lower())
    terms, seen_terms = [], set()
    for w in words:
        if w in _STOPWORDS or w in seen_terms:
            continue
        seen_terms.add(w)
        terms.append(w)
        if len(terms) >= max_terms:
            break
    merged, seen = [], set()
    for t in terms:
        for r in search_skills(t, limit=3):
            key = (r["owner_repo"], r["skill"])
            if key not in seen:
                seen.add(key)
                merged.append(r)
    merged.sort(key=lambda r: r["installs"], reverse=True)
    return merged[:limit]


def find_skill(name: str) -> dict | None:
    """Localiza uma skill pelo nome: cache local primeiro, depois busca no skills.sh."""
    row = db.query_one(
        "SELECT owner_repo, skill, installs FROM skills_cache "
        "WHERE skill = ? ORDER BY installs DESC LIMIT 1",
        (name,),
    )
    if row:
        return row
    # a IA pediu uma skill fora do cache: valida direto na busca do skills.sh
    for r in search_skills(name, limit=3):
        if r["skill"] == name:
            return r
    return None


def install_skill(project_dir: str, owner_repo: str, skill: str, timeout: int = 300) -> dict:
    """Instala uma skill no projeto via `npx skills add` (formato nativo, portátil)."""
    from . import bootstrap
    npx = bootstrap.find_node_tool("npx")
    if not npx:
        return {"ok": False, "output": "npx não encontrado (instale Node.js)."}
    cmd = ["cmd", "/c", npx, "-y", "skills", "add", owner_repo,
           "-s", skill, "-a", "claude-code", "-y", "--copy"]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, cwd=project_dir,
            timeout=timeout, encoding="utf-8", errors="replace",
            creationflags=config.CREATE_NO_WINDOW,
        )
        ok = proc.returncode == 0
        out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        return {"ok": ok, "output": out[-2000:]}
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "output": f"Falha ao instalar skill: {e}"}
