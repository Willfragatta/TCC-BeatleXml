"""Rate limit de autenticação persistido no Postgres (compartilhado entre workers)."""
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.models.database import AuthTentativa

MSG_LIMITE = "Muitas tentativas. Aguarde alguns minutos e tente de novo."

LIMITES = {
    "login": {"janela_min": 15, "por_ip": 25, "por_email": 12},
    "cadastro": {"janela_min": 15, "por_ip": 3, "por_email": 0},
    "reset": {"janela_min": 60, "por_ip": 3, "por_email": 0},
    "confirmar": {"janela_min": 15, "por_ip": 8, "por_email": 4},
    "otp": {"janela_min": 15, "por_ip": 10, "por_email": 6},
    "contato": {"janela_min": 15, "por_ip": 8, "por_email": 4},
    "uploader_login": {"janela_min": 15, "por_ip": 10, "por_email": 0},
    "contador_login": {"janela_min": 15, "por_ip": 10, "por_email": 0},
}


def _contar(db: Session, acao: str, desde: datetime, ip: Optional[str] = None, email: Optional[str] = None) -> int:
    q = db.query(AuthTentativa).filter(
        AuthTentativa.acao == acao,
        AuthTentativa.created_at >= desde,
    )
    if ip is not None:
        q = q.filter(AuthTentativa.ip == ip)
    if email is not None:
        q = q.filter(AuthTentativa.email == email)
    return q.count()


def verificar_limite(
    db: Session,
    acao: str,
    ip: str,
    email: Optional[str] = None,
) -> Optional[str]:
    cfg = LIMITES.get(acao) or LIMITES["login"]
    desde = datetime.now() - timedelta(minutes=cfg["janela_min"])
    try:
        if cfg["por_ip"] and _contar(db, acao, desde, ip=ip) >= cfg["por_ip"]:
            return MSG_LIMITE
        email_norm = (email or "").strip().lower()
        if cfg["por_email"] and email_norm:
            if _contar(db, acao, desde, email=email_norm) >= cfg["por_email"]:
                return MSG_LIMITE
    except Exception:
        db.rollback()
    return None


def registrar_tentativa(db: Session, acao: str, ip: str, email: Optional[str] = None) -> None:
    try:
        db.add(AuthTentativa(
            ip=(ip or "0.0.0.0")[:45],
            email=(email or "").strip().lower()[:255] or None,
            acao=acao,
            created_at=datetime.now(),
        ))
        db.commit()
    except Exception:
        db.rollback()


def limpar_sucesso(db: Session, acao: str, email: Optional[str] = None, ip: Optional[str] = None) -> None:
    try:
        q = db.query(AuthTentativa).filter(AuthTentativa.acao == acao)
        if email:
            q = q.filter(AuthTentativa.email == email.strip().lower())
        elif ip:
            q = q.filter(AuthTentativa.ip == ip)
        else:
            return
        q.delete(synchronize_session=False)
        db.commit()
    except Exception:
        db.rollback()
