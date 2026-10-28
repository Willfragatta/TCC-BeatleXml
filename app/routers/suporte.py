from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import List, Optional
import base64
import secrets

from app.core.dependencies import get_db, get_suporte_atual
from app.core.templates import templates
from app.core.paginas import PAGINAS_CATALOGO, PAGINAS_OPCOES, PAGINAS_PADRAO
from app.models.database import Empresa, Usuario, UsuarioEmpresa, SuporteLog, BaseConsulta
from app.core.security import descriptografar
from app.core.settings import site_url
from app.models.password_reset import PasswordReset
from app.services.email_envio import enviar_reset_senha
from app.services.suporte_logs import registrar_log_suporte, registrar_acao_suporte
from worker import consultar_max_nsu

router = APIRouter()

class AjusteNSURequest(BaseModel):
    ultimo_nsu: str | None = None
    max_nsu_conhecido: str | None = None


class BloqueioManualRequest(BaseModel):
    minutos: int

def _normalizar_nsu(valor: str | None):
    if valor is None:
        return None
    valor_limpo = "".join(filter(str.isdigit, str(valor)))
    if not valor_limpo:
        raise ValueError("NSU vazio")
    if len(valor_limpo) > 15:
        raise ValueError("NSU deve ter até 15 dígitos")
    return valor_limpo.zfill(15)


def _verificar_validade_certificado(cert_pfx_bytes, cert_senha):
    try:
        from cryptography.hazmat.primitives.serialization import pkcs12
        from cryptography.hazmat.backends import default_backend
        private_key, certificate, additional_certificates = pkcs12.load_key_and_certificates(
            cert_pfx_bytes, cert_senha.encode(), default_backend()
        )
        if certificate is None:
            return None, None
        validade_naive = certificate.not_valid_after
        if validade_naive.tzinfo is None:
            validade = validade_naive
        else:
            validade = validade_naive.replace(tzinfo=None)
        dias_restantes = (validade - datetime.now()).days
        return validade, dias_restantes
    except Exception:
        return None, None

def _extrair_cnpj_base(cnpj):
    cnpj_limpo = "".join(filter(str.isdigit, str(cnpj)))
    return cnpj_limpo[:8] if len(cnpj_limpo) >= 8 else None


def _ids_empresas_usuario(db: Session, usuario_id: int) -> set:
    ids = {eid for (eid,) in db.query(Empresa.id).filter_by(usuario_id=usuario_id).all()}
    ids.update(
        eid for (eid,) in db.query(UsuarioEmpresa.empresa_id).filter_by(usuario_id=usuario_id).all()
    )
    return ids

@router.get("/suporte")
async def suporte_home(
    request: Request,
    usuario = Depends(get_suporte_atual)
):
    return templates.TemplateResponse(
        request,
        "suporte/index.html",
        {"usuario": usuario},
    )


@router.get("/suporte/atendimento")
async def suporte_atendimento_page(
    request: Request,
    usuario=Depends(get_suporte_atual),
):
    return templates.TemplateResponse(
        request,
        "suporte/atendimento.html",
        {"usuario": usuario},
    )


@router.get("/suporte/usuarios")
async def suporte_usuarios_page(
    request: Request,
    usuario = Depends(get_suporte_atual),
):
    return templates.TemplateResponse(
        request,
        "suporte/usuarios.html",
        {"usuario": usuario},
    )

@router.get("/api/suporte/empresas")
async def suporte_empresas(
    usuario_id: int | None = None,
    usuario = Depends(get_suporte_atual),
    db: Session = Depends(get_db)
):
    query = db.query(Empresa)
    if usuario_id:
        ids = _ids_empresas_usuario(db, usuario_id)
        if not ids:
            return []
        query = query.filter(Empresa.id.in_(ids))
    empresas = query.all()
    resultado = []
    for empresa in empresas:
        tem_certificado = bool(empresa.certificado_base64)
        validade = None
        dias_para_vencer = None
        if tem_certificado:
            try:
                cert_pfx_bytes = base64.b64decode(descriptografar(empresa.certificado_base64))
                cert_senha = descriptografar(empresa.certificado_senha)
                validade, dias_para_vencer = _verificar_validade_certificado(cert_pfx_bytes, cert_senha)
            except Exception:
                pass
        resultado.append({
            "id": empresa.id,
            "cnpj": empresa.cnpj,
            "razao_social": empresa.razao_social,
            "status": empresa.status,
            "ambiente": empresa.ambiente,
            "uf_autor": empresa.uf_autor,
            "ultimo_nsu": empresa.ultimo_nsu,
            "max_nsu_conhecido": empresa.max_nsu_conhecido,
            "ultima_consulta": empresa.ultima_consulta,
            "bloqueio_656_ate": empresa.bloqueio_656_ate,
            "bloqueio_manual_ate": empresa.bloqueio_manual_ate,
            "proxima_consulta_periodica": empresa.proxima_consulta_periodica,
            "tem_certificado": tem_certificado,
            "certificado_valido_ate": validade,
            "dias_para_vencer": dias_para_vencer
        })
    return resultado


@router.get("/api/suporte/uploader-status")
async def suporte_uploader_status(
    usuario_id: int | None = None,
    usuario=Depends(get_suporte_atual),
    db: Session = Depends(get_db),
) -> List[dict]:
    """Status do Beatle Uploader e do sincronizador do contador."""
    query = db.query(Empresa)
    if usuario_id:
        ids = _ids_empresas_usuario(db, usuario_id)
        if not ids:
            return []
        query = query.filter(Empresa.id.in_(ids))
    empresas = query.all()
    agora = datetime.now()
    limite_online_minutos = 10

    resultado = []
    for emp in empresas:
        ultimo = emp.uploader_ultimo_heartbeat
        online = False
        if ultimo:
            delta = (agora - ultimo).total_seconds()
            online = delta < (limite_online_minutos * 60)
        hb_contador = getattr(emp, "contador_ultimo_heartbeat", None)
        contador_online = False
        if hb_contador:
            contador_online = (agora - hb_contador).total_seconds() < (limite_online_minutos * 60)

        resultado.append({
            "id": emp.id,
            "razao_social": emp.razao_social,
            "cnpj": emp.cnpj,
            "ultimo_heartbeat": ultimo.isoformat() if ultimo else None,
            "pasta_monitorada": emp.uploader_pasta_monitorada,
            "versao": emp.uploader_versao,
            "contador_ultimo_heartbeat": hb_contador.isoformat() if hb_contador else None,
            "contador_online": contador_online,
            "contador_versao": getattr(emp, "contador_versao", None),
            "online": online,
        })

    return resultado


@router.get("/api/suporte/logs")
async def suporte_logs(
    empresa_id: int | None = None,
    nivel: str | None = None,
    tipo: str | None = None,
    texto: str | None = None,
    limit: int = 200,
    usuario = Depends(get_suporte_atual),
    db: Session = Depends(get_db)
):
    query = db.query(SuporteLog).order_by(SuporteLog.created_at.desc())
    if empresa_id:
        query = query.filter(SuporteLog.empresa_id == empresa_id)
    if nivel:
        query = query.filter(SuporteLog.nivel == nivel.upper())
    if tipo:
        query = query.filter(SuporteLog.tipo == tipo)
    if texto:
        query = query.filter(SuporteLog.mensagem.ilike(f"%{texto}%"))

    logs = query.limit(limit).all()
    return [{
        "id": log.id,
        "empresa_id": log.empresa_id,
        "nivel": log.nivel,
        "tipo": log.tipo,
        "mensagem": log.mensagem,
        "cstat": log.cstat,
        "ult_nsu": log.ult_nsu,
        "max_nsu": log.max_nsu,
        "contexto": log.contexto,
        "created_at": log.created_at
    } for log in logs]

@router.get("/api/suporte/timeline")
async def suporte_timeline(
    empresa_id: int | None = None,
    cnpj_base: str | None = None,
    ciclo_id: str | None = None,
    limit: int = 200,
    usuario = Depends(get_suporte_atual),
    db: Session = Depends(get_db)
):
    query = db.query(SuporteLog).filter(SuporteLog.tipo == "timeline").order_by(SuporteLog.created_at.desc())
    if empresa_id:
        query = query.filter(SuporteLog.empresa_id == empresa_id)
    if cnpj_base:
        query = query.filter(SuporteLog.contexto.ilike(f"%\"cnpj_base\": \"{cnpj_base}\"%"))
    if ciclo_id:
        query = query.filter(SuporteLog.contexto.ilike(f"%\"ciclo_id\": \"{ciclo_id}\"%"))

    logs = query.limit(limit).all()
    return [{
        "id": log.id,
        "empresa_id": log.empresa_id,
        "mensagem": log.mensagem,
        "contexto": log.contexto,
        "created_at": log.created_at
    } for log in logs]

@router.post("/api/suporte/empresa/{empresa_id}/ajustar-nsu")
async def suporte_ajustar_nsu(
    empresa_id: int,
    payload: AjusteNSURequest,
    usuario = Depends(get_suporte_atual),
    db: Session = Depends(get_db)
):
    empresa = db.query(Empresa).filter_by(id=empresa_id).first()
    if not empresa:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")

    try:
        novo_ult = _normalizar_nsu(payload.ultimo_nsu) if payload.ultimo_nsu is not None else None
        novo_max = _normalizar_nsu(payload.max_nsu_conhecido) if payload.max_nsu_conhecido is not None else None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if novo_ult is not None:
        empresa.ultimo_nsu = novo_ult
    if novo_max is not None:
        empresa.max_nsu_conhecido = novo_max

    db.commit()
    registrar_acao_suporte(empresa_id, usuario.id, "ajustar_nsu", payload.dict())
    registrar_log_suporte(
        empresa_id=empresa_id,
        nivel="INFO",
        tipo="nsu",
        mensagem="NSU ajustado pelo suporte",
        ult_nsu=empresa.ultimo_nsu,
        max_nsu=empresa.max_nsu_conhecido
    )
    return {"status": "ok", "ultimo_nsu": empresa.ultimo_nsu, "max_nsu_conhecido": empresa.max_nsu_conhecido}


@router.post("/api/suporte/empresa/{empresa_id}/bloqueio-manual")
async def suporte_bloqueio_manual(
    empresa_id: int,
    payload: BloqueioManualRequest,
    usuario = Depends(get_suporte_atual),
    db: Session = Depends(get_db)
):
    empresa = db.query(Empresa).filter_by(id=empresa_id).first()
    if not empresa:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    if payload.minutos <= 0:
        raise HTTPException(status_code=400, detail="Minutos deve ser maior que zero")

    empresa.bloqueio_manual_ate = datetime.now() + timedelta(minutes=payload.minutos)
    db.commit()
    registrar_acao_suporte(empresa_id, usuario.id, "bloqueio_manual", {"minutos": payload.minutos})
    registrar_log_suporte(
        empresa_id=empresa_id,
        nivel="WARNING",
        tipo="bloqueio",
        mensagem=f"Bloqueio manual aplicado por {payload.minutos} minutos"
    )
    return {"status": "ok", "bloqueio_manual_ate": empresa.bloqueio_manual_ate}

@router.post("/api/suporte/empresa/{empresa_id}/liberar-bloqueio-manual")
async def suporte_liberar_bloqueio_manual(
    empresa_id: int,
    usuario = Depends(get_suporte_atual),
    db: Session = Depends(get_db)
):
    empresa = db.query(Empresa).filter_by(id=empresa_id).first()
    if not empresa:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    empresa.bloqueio_manual_ate = None
    db.commit()
    registrar_acao_suporte(empresa_id, usuario.id, "liberar_bloqueio_manual")
    registrar_log_suporte(
        empresa_id=empresa_id,
        nivel="INFO",
        tipo="bloqueio",
        mensagem="Bloqueio manual removido pelo suporte"
    )
    return {"status": "ok"}

@router.post("/api/suporte/empresa/{empresa_id}/consulta-leve")
async def suporte_consulta_leve(
    empresa_id: int,
    usuario = Depends(get_suporte_atual),
    db: Session = Depends(get_db)
):
    empresa = db.query(Empresa).filter_by(id=empresa_id).first()
    if not empresa:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")

    if not empresa.certificado_base64 or not empresa.certificado_senha:
        raise HTTPException(status_code=400, detail="Certificado não cadastrado")

    try:
        pfx_bytes = base64.b64decode(descriptografar(empresa.certificado_base64))
        cert_senha = descriptografar(empresa.certificado_senha)
    except Exception:
        raise HTTPException(status_code=400, detail="Falha ao carregar certificado")

    base_consulta = None
    cnpj_base = _extrair_cnpj_base(empresa.cnpj)
    if cnpj_base:
        base_consulta = db.query(BaseConsulta).filter_by(cnpj_base=cnpj_base, ambiente=empresa.ambiente).first()

    max_nsu_novo, ult_nsu_novo, cstat, _ = consultar_max_nsu(
        empresa, pfx_bytes, cert_senha, empresa.ambiente, empresa.uf_autor, base_consulta
    )
    if base_consulta:
        db.commit()

    registrar_acao_suporte(empresa_id, usuario.id, "consulta_leve")
    registrar_log_suporte(
        empresa_id=empresa_id,
        nivel="INFO",
        tipo="sefaz",
        mensagem="Consulta leve executada pelo suporte",
        cstat=cstat,
        ult_nsu=ult_nsu_novo,
        max_nsu=max_nsu_novo
    )

    return {"cstat": cstat, "ult_nsu": ult_nsu_novo, "max_nsu": max_nsu_novo}


class UsuarioSuporteUpdate(BaseModel):
    role: Optional[str] = None
    status: Optional[str] = None
    nome_completo: Optional[str] = None
    telefone: Optional[str] = None
    email: Optional[str] = None
    vinculos: Optional[List[dict]] = None


class EmpresaAprovarRequest(BaseModel):
    acao: str
    motivo: Optional[str] = None


def _serializar_usuario_suporte(db: Session, u: Usuario) -> dict:
    empresas_dono = db.query(Empresa).filter_by(usuario_id=u.id).all()
    vinculos = db.query(UsuarioEmpresa).filter_by(usuario_id=u.id).all()
    por_emp = {v.empresa_id: v for v in vinculos}
    ids = {e.id for e in empresas_dono} | set(por_emp.keys())
    empresas = db.query(Empresa).filter(Empresa.id.in_(ids)).all() if ids else []
    return {
        "id": u.id,
        "email": u.email,
        "nome_completo": u.nome_completo,
        "telefone": u.telefone,
        "status": u.status,
        "role": getattr(u, "role", "cliente"),
        "is_admin": bool(getattr(u, "is_admin", False)),
        "email_confirmado": bool(getattr(u, "email_confirmado_em", None)),
        "created_at": u.created_at.isoformat() if u.created_at else None,
        "last_login": u.last_login.isoformat() if u.last_login else None,
        "empresas": [
            {
                "id": e.id,
                "razao_social": e.razao_social,
                "cnpj": e.cnpj,
                "status": e.status,
                "papel": (por_emp[e.id].papel if e.id in por_emp else "empresa_master"),
                "paginas_permitidas": (
                    por_emp[e.id].paginas_permitidas if e.id in por_emp else None
                ),
            }
            for e in empresas
        ],
    }


@router.get("/api/suporte/pendentes-resumo")
async def suporte_pendentes_resumo(
    usuario=Depends(get_suporte_atual),
    db: Session = Depends(get_db),
):
    return {
        "usuarios": db.query(Usuario).filter_by(status="pendente").count(),
        "empresas": db.query(Empresa).filter_by(status="pendente_aprovacao").count(),
    }


@router.get("/api/suporte/paginas-meta")
async def suporte_paginas_meta(usuario=Depends(get_suporte_atual)):
    return {
        "paginas": [{"path": p, "label": l} for p, l in PAGINAS_CATALOGO],
        "papeis": list(PAGINAS_PADRAO.keys()),
        "roles": ["cliente", "contador", "suporte"],
        "padrao": {k: sorted(v) for k, v in PAGINAS_PADRAO.items()},
    }


@router.get("/api/suporte/usuarios")
async def suporte_listar_usuarios(
    status_filtro: Optional[str] = Query(None, alias="status"),
    usuario=Depends(get_suporte_atual),
    db: Session = Depends(get_db),
):
    q = db.query(Usuario).order_by(Usuario.created_at.desc(), Usuario.id.desc())
    if status_filtro == "bloqueados":
        q = q.filter(Usuario.status.in_(["suspenso", "cancelado"]))
    elif status_filtro:
        q = q.filter(Usuario.status == status_filtro)
    return [_serializar_usuario_suporte(db, u) for u in q.all()]


@router.post("/api/suporte/usuarios/{usuario_id}/aprovar")
async def suporte_aprovar_usuario(
    usuario_id: int,
    usuario=Depends(get_suporte_atual),
    db: Session = Depends(get_db),
):
    alvo = db.query(Usuario).filter_by(id=usuario_id).first()
    if not alvo:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    alvo.status = "ativo"
    db.commit()
    registrar_acao_suporte(None, usuario.id, "aprovar_usuario", {"usuario_id": usuario_id, "email": alvo.email})
    return {"ok": True, "status": "ativo"}


@router.post("/api/suporte/usuarios/{usuario_id}/bloquear")
async def suporte_bloquear_usuario(
    usuario_id: int,
    usuario=Depends(get_suporte_atual),
    db: Session = Depends(get_db),
):
    if usuario_id == usuario.id:
        raise HTTPException(status_code=400, detail="Você não pode bloquear a própria conta.")
    alvo = db.query(Usuario).filter_by(id=usuario_id).first()
    if not alvo:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    if getattr(alvo, "is_admin", False) and not getattr(usuario, "is_admin", False):
        raise HTTPException(status_code=403, detail="Somente admin pode bloquear outro admin.")
    alvo.status = "suspenso"
    db.commit()
    registrar_acao_suporte(None, usuario.id, "bloquear_usuario", {"usuario_id": usuario_id, "email": alvo.email})
    return {"ok": True, "status": "suspenso"}


@router.post("/api/suporte/usuarios/{usuario_id}/liberar")
async def suporte_liberar_usuario(
    usuario_id: int,
    usuario=Depends(get_suporte_atual),
    db: Session = Depends(get_db),
):
    alvo = db.query(Usuario).filter_by(id=usuario_id).first()
    if not alvo:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    alvo.status = "ativo"
    db.commit()
    registrar_acao_suporte(None, usuario.id, "liberar_usuario", {"usuario_id": usuario_id, "email": alvo.email})
    return {"ok": True, "status": "ativo"}


@router.put("/api/suporte/usuarios/{usuario_id}")
async def suporte_editar_usuario(
    usuario_id: int,
    payload: UsuarioSuporteUpdate,
    usuario=Depends(get_suporte_atual),
    db: Session = Depends(get_db),
):
    alvo = db.query(Usuario).filter_by(id=usuario_id).first()
    if not alvo:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    if getattr(alvo, "is_admin", False) and not getattr(usuario, "is_admin", False):
        raise HTTPException(status_code=403, detail="Somente admin pode editar outro admin.")

    if payload.role is not None:
        if payload.role not in ("cliente", "contador", "suporte"):
            raise HTTPException(status_code=400, detail="Perfil inválido.")
        if usuario_id == usuario.id and payload.role != getattr(usuario, "role", None):
            raise HTTPException(status_code=400, detail="Não altere o próprio perfil por aqui.")
        alvo.role = payload.role

    if payload.nome_completo is not None:
        alvo.nome_completo = payload.nome_completo.strip()[:200]
    if payload.telefone is not None:
        alvo.telefone = payload.telefone.strip()[:20] or None
    if payload.email is not None:
        email_novo = payload.email.strip().lower()
        if "@" not in email_novo:
            raise HTTPException(status_code=400, detail="E-mail inválido.")
        outro = db.query(Usuario).filter(Usuario.email == email_novo, Usuario.id != alvo.id).first()
        if outro:
            raise HTTPException(status_code=400, detail="Este e-mail já está em uso.")
        alvo.email = email_novo

    if payload.status is not None:
        if payload.status not in ("pendente", "ativo", "suspenso", "cancelado"):
            raise HTTPException(status_code=400, detail="Status inválido.")
        if usuario_id == usuario.id and payload.status != "ativo":
            raise HTTPException(status_code=400, detail="Você não pode desativar a própria conta.")
        alvo.status = payload.status

    if payload.vinculos is not None:
        for item in payload.vinculos:
            eid = int(item.get("empresa_id") or 0)
            if not eid:
                continue
            empresa = db.query(Empresa).filter_by(id=eid).first()
            if not empresa:
                continue
            papel = item.get("papel") or "operador"
            if papel not in ("empresa_master", "operador", "leitura"):
                papel = "operador"
            paginas = item.get("paginas_permitidas")
            if paginas is not None:
                paginas = [p for p in paginas if p in PAGINAS_OPCOES]
            vinculo = (
                db.query(UsuarioEmpresa)
                .filter_by(usuario_id=alvo.id, empresa_id=eid)
                .first()
            )
            if vinculo is None:
                vinculo = UsuarioEmpresa(
                    usuario_id=alvo.id,
                    empresa_id=eid,
                    papel=papel,
                    paginas_permitidas=paginas,
                    created_at=datetime.now(),
                )
                db.add(vinculo)
            else:
                vinculo.papel = papel
                vinculo.paginas_permitidas = paginas

    db.commit()
    registrar_acao_suporte(None, usuario.id, "editar_usuario", {"usuario_id": usuario_id})
    return _serializar_usuario_suporte(db, alvo)


@router.post("/api/suporte/usuarios/{usuario_id}/reenviar-senha")
async def suporte_reenviar_senha(
    usuario_id: int,
    usuario=Depends(get_suporte_atual),
    db: Session = Depends(get_db),
):
    """Envia o link de definição de senha. A senha não volta na resposta."""
    alvo = db.query(Usuario).filter_by(id=usuario_id).first()
    if not alvo:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now() + timedelta(hours=1)
    db.query(PasswordReset).filter_by(usuario_id=alvo.id, used=False).update({"used": True})
    db.add(PasswordReset(usuario_id=alvo.id, token=token, expires_at=expires_at))
    db.commit()
    link = f"{site_url()}/auth/reset-password?token={token}"
    enviar_reset_senha(alvo.email, alvo.nome_completo or alvo.email, link)
    registrar_acao_suporte(None, usuario.id, "reenviar_senha", {"usuario_id": usuario_id, "email": alvo.email})
    return {"ok": True, "message": "Link enviado para o e-mail do usuário."}


@router.get("/api/suporte/empresas-pendentes")
async def suporte_empresas_pendentes(
    usuario=Depends(get_suporte_atual),
    db: Session = Depends(get_db),
):
    empresas = db.query(Empresa).filter_by(status="pendente_aprovacao").order_by(Empresa.id.desc()).all()
    resultado = []
    for empresa in empresas:
        dono = db.query(Usuario).filter_by(id=empresa.usuario_id).first() if empresa.usuario_id else None
        resultado.append({
            "id": empresa.id,
            "cnpj": empresa.cnpj,
            "razao_social": empresa.razao_social,
            "status": empresa.status,
            "usuario_email": dono.email if dono else None,
            "usuario_nome": dono.nome_completo if dono else None,
            "usuario_id": empresa.usuario_id,
        })
    return resultado


@router.post("/api/suporte/empresas/{empresa_id}/aprovar")
async def suporte_aprovar_empresa(
    empresa_id: int,
    payload: EmpresaAprovarRequest,
    usuario=Depends(get_suporte_atual),
    db: Session = Depends(get_db),
):
    empresa = db.query(Empresa).filter_by(id=empresa_id).first()
    if not empresa:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")

    if payload.acao == "aprovar":
        empresa.status = "aprovado"
        empresa.data_aprovacao = datetime.now()
        empresa.data_ativacao = datetime.now()
        if not empresa.max_nsu_conhecido or empresa.max_nsu_conhecido == "000000000000000":
            if empresa.ultimo_nsu and empresa.ultimo_nsu not in ("0", "000000000000000"):
                empresa.max_nsu_conhecido = empresa.ultimo_nsu.zfill(15)
            else:
                empresa.max_nsu_conhecido = "000000000000000"
        if empresa.ultimo_nsu and len(empresa.ultimo_nsu) < 15:
            empresa.ultimo_nsu = empresa.ultimo_nsu.zfill(15)
        db.commit()
        registrar_acao_suporte(empresa_id, usuario.id, "aprovar_empresa", {"motivo": payload.motivo})
        return {"ok": True, "status": "aprovado"}

    if payload.acao == "rejeitar":
        meta = {"empresa_id": empresa_id, "cnpj": empresa.cnpj, "razao": empresa.razao_social, "motivo": payload.motivo}
        db.delete(empresa)
        db.commit()
        registrar_acao_suporte(None, usuario.id, "rejeitar_empresa", meta)
        return {"ok": True, "status": "rejeitado"}

    raise HTTPException(status_code=400, detail="Ação inválida. Use aprovar ou rejeitar.")
