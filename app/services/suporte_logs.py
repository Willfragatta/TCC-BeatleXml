from datetime import datetime, timedelta
import json

from app.models.database import SessionLocal, SuporteLog, SuporteAcao

_ultima_limpeza = None

def _deve_limpar_logs():
    global _ultima_limpeza
    agora = datetime.now()
    if _ultima_limpeza is None or (agora - _ultima_limpeza) > timedelta(hours=24):
        _ultima_limpeza = agora
        return True
    return False

def limpar_logs_antigos(dias=90):
    db = SessionLocal()
    try:
        limite = datetime.now() - timedelta(days=dias)
        db.query(SuporteLog).filter(SuporteLog.created_at < limite).delete()
        db.commit()
    finally:
        db.close()

def registrar_log_suporte(
    empresa_id=None,
    nivel="INFO",
    tipo="sistema",
    mensagem="",
    cstat=None,
    ult_nsu=None,
    max_nsu=None,
    contexto=None
):
    db = SessionLocal()
    try:
        log = SuporteLog(
            empresa_id=empresa_id,
            nivel=nivel,
            tipo=tipo,
            mensagem=mensagem,
            cstat=cstat,
            ult_nsu=ult_nsu,
            max_nsu=max_nsu,
            contexto=json.dumps(contexto, ensure_ascii=True) if isinstance(contexto, (dict, list)) else contexto
        )
        db.add(log)
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()

    if _deve_limpar_logs():
        limpar_logs_antigos(dias=90)

def registrar_acao_suporte(empresa_id, usuario_suporte_id, acao, payload=None):
    db = SessionLocal()
    try:
        acao_reg = SuporteAcao(
            empresa_id=empresa_id,
            usuario_suporte_id=usuario_suporte_id,
            acao=acao,
            payload=json.dumps(payload, ensure_ascii=True) if isinstance(payload, (dict, list)) else payload
        )
        db.add(acao_reg)
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()
