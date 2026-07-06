"""Governança (inspirada no núcleo do Paperclip, simplificada):

- Níveis de autonomia por missão (0 sugestão / 1 aprovação obrigatória / 3 autônomo);
- Termo de Ciência e Assunção de Risco com consentimento registrado (hash + host);
- Aprovações (plano e budget) resolvidas pela UI;
- Budget hard-stop por execução.
"""
import hashlib
import socket

from . import db

TERM_VERSION = "1.0"

RISK_TERM = """TERMO DE CIÊNCIA E ASSUNÇÃO DE RISCO — AUTONOMIA DE AGENTES (v1.0)

Você está habilitando o modo AUTÔNOMO para esta missão. Ao aceitar, você declara que:

1. Compreende que agentes de IA podem errar e que ações autônomas podem causar
   danos financeiros, jurídicos ou reputacionais.
2. Assume integral responsabilidade pelas ações executadas autonomamente pelos
   agentes que você configurou nesta missão.
3. Reconhece que o padrão seguro é a Aprovação Obrigatória (nível 1), e que a
   elevação de autonomia é decisão sua.
4. O aceite fica registrado de forma imutável (data/hora, computador, versão e
   hash deste termo) na trilha de auditoria.
"""

AUTONOMY_LEVELS = {
    0: "Somente sugestão (planeja, não executa)",
    1: "Aprovação obrigatória (padrão seguro)",
    3: "Autônomo (requer aceite do termo de risco)",
    4: "Só se vive uma vez — auto-aprova TUDO, inclusive budget (YOLO total)",
}


def term_hash() -> str:
    return hashlib.sha256(RISK_TERM.encode("utf-8")).hexdigest()


def record_consent(scope: str, level: int, details: str = "") -> int:
    """Registra o aceite do termo (imutável) e devolve o id."""
    cid = db.execute(
        "INSERT INTO consents(ts, scope, level, term_version, term_hash, user_host, details) "
        "VALUES(?,?,?,?,?,?,?)",
        (db.now(), scope, level, TERM_VERSION, term_hash(),
         socket.gethostname(), details),
    )
    db.add_event(
        f"Consentimento registrado: nível {level} para {scope} "
        f"(termo v{TERM_VERSION}, hash {term_hash()[:12]}…)",
        level="info",
    )
    return cid


def create_approval(run_id: int, task_id: int, kind: str, payload: str) -> int:
    aid = db.execute(
        "INSERT INTO approvals(run_id, task_id, kind, payload, created_at) VALUES(?,?,?,?,?)",
        (run_id, task_id, kind, payload, db.now()),
    )
    db.add_event(f"Decisão pendente ({kind}): aguardando aprovação do gestor.",
                 run_id=run_id, task_id=task_id, level="warn")
    return aid


def resolve_approval(approval_id: int, approve: bool, reason: str = "") -> dict | None:
    a = db.query_one("SELECT * FROM approvals WHERE id=? AND status='pendente'", (approval_id,))
    if not a:
        return None
    status = "aprovada" if approve else "rejeitada"
    db.execute("UPDATE approvals SET status=?, reason=?, resolved_at=? WHERE id=?",
               (status, (reason or "")[:2000], db.now(), approval_id))
    db.add_event(f"Decisão {status} pelo gestor ({a['kind']})."
                 + (f" Motivo: {reason[:300]}" if reason else ""),
                 run_id=a["run_id"], task_id=a["task_id"])
    return {**a, "status": status, "reason": reason}
