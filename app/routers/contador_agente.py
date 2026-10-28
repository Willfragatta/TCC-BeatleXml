"""API do programa Windows do contador.

O certificado fica no servidor. Este módulo só lista empresas aprovadas,
dispara a sincronização já existente e registra o heartbeat.
"""
import asyncio
import base64
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.auth import criar_token, verificar_senha
from app.core.dependencies import get_db, get_usuario_atual, ids_empresas_acessiveis
from app.core.rate_limit import limpar_sucesso, registrar_tentativa, verificar_limite
from app.core.recaptcha import client_ip
from app.models.database import ContadorSyncEmpresa, Empresa, Usuario
from app.services.contador_painel import (
    cnpj_base,
    deve_retomar,
    estado_da_empresa,
    faixa_atencao,
    momento_retomada,
    montar_painel,
)
import worker

router = APIRouter()
VERSAO_PADRAO = "1.0.0"


class LoginContadorRequest(BaseModel):
    email: str
    senha: str


class HeartbeatContadorRequest(BaseModel):
    empresa_id: int
    versao: Optional[str] = None


class EmpresaSyncBody(BaseModel):
    ativo: bool
    retomar_automatico: bool = True


class SyncPararBody(BaseModel):
    retomar_automatico: bool = True


def estado_sync(empresa: Empresa, agora: datetime | None = None) -> str:
    agora = agora or datetime.now()
    return estado_da_empresa(
        getattr(empresa, "status", None),
        empresa.bloqueio_656_ate,
        empresa.bloqueio_manual_ate,
        empresa.ultimo_nsu,
        empresa.max_nsu_conhecido,
        agora,
    )


def _garantir_flags_sync(db: Session, usuario: Usuario, empresa_ids: list[int]) -> dict[int, bool]:
    """Garante linha por empresa; default ativo=True. Retorna mapa empresa_id -> ativo."""
    if not empresa_ids:
        return {}
    existentes = (
        db.query(ContadorSyncEmpresa)
        .filter(
            ContadorSyncEmpresa.usuario_id == usuario.id,
            ContadorSyncEmpresa.empresa_id.in_(empresa_ids),
        )
        .all()
    )
    mapa = {r.empresa_id: bool(r.ativo) for r in existentes}
    criou = False
    for eid in empresa_ids:
        if eid not in mapa:
            db.add(
                ContadorSyncEmpresa(
                    usuario_id=usuario.id,
                    empresa_id=eid,
                    ativo=True,
                    updated_at=datetime.now(),
                )
            )
            mapa[eid] = True
            criou = True
    if criou:
        db.commit()
    return mapa


def _sync_row(db: Session, usuario: Usuario, empresa_id: int) -> Optional[ContadorSyncEmpresa]:
    return (
        db.query(ContadorSyncEmpresa)
        .filter_by(usuario_id=usuario.id, empresa_id=empresa_id)
        .first()
    )


def _sync_ativo_empresa(db: Session, usuario: Usuario, empresa_id: int) -> bool:
    row = _sync_row(db, usuario, empresa_id)
    if row is None:
        return True
    return bool(row.ativo)


def _iso(valor) -> Optional[str]:
    return valor.isoformat() if valor else None


def _preencher_validade_certificado(db: Session, empresa: Empresa) -> None:
    """Descriptografa o PFX só enquanto a validade ainda não foi gravada."""
    if getattr(empresa, "certificado_valido_ate", None):
        return
    if not empresa.certificado_base64 or not empresa.certificado_senha:
        return
    try:
        from app.core.security import descriptografar
        from app.routers.empresas import verificar_validade_certificado

        cert_pfx_bytes = base64.b64decode(descriptografar(empresa.certificado_base64))
        cert_senha = descriptografar(empresa.certificado_senha)
        validade, _dias = verificar_validade_certificado(cert_pfx_bytes, cert_senha)
    except Exception:
        return
    if not validade:
        return
    empresa.certificado_valido_ate = validade
    db.commit()


def _aplicar_retomadas(db: Session, usuario: Usuario) -> bool:
    """Religa busca global ou de empresa quando o prazo de 2 horas venceu."""
    agora = datetime.now()
    retomou = False
    if not usuario.contador_agente_ativo and deve_retomar(usuario.contador_agente_retomar_em, agora):
        usuario.contador_agente_ativo = True
        usuario.contador_agente_pausado_em = None
        usuario.contador_agente_retomar_em = None
        retomou = True
    linhas = (
        db.query(ContadorSyncEmpresa)
        .filter(ContadorSyncEmpresa.usuario_id == usuario.id)
        .all()
    )
    for linha in linhas:
        if not linha.ativo and deve_retomar(linha.retomar_em, agora):
            linha.ativo = True
            linha.pausado_em = None
            linha.retomar_em = None
            linha.updated_at = agora
            retomou = True
    if retomou:
        db.commit()
    return retomou


def _resumo(
    empresa: Empresa,
    *,
    sync_ativo: bool = True,
    agente_ativo: bool = False,
    pausado_em=None,
    retomar_em=None,
    agente_retomar_em=None,
    retomada_automatica: bool = False,
    faixa: str = "",
    agora: datetime | None = None,
) -> dict:
    agora = agora or datetime.now()
    painel = montar_painel(
        agora=agora,
        status=getattr(empresa, "status", None),
        bloqueio_656_ate=empresa.bloqueio_656_ate,
        bloqueio_manual_ate=empresa.bloqueio_manual_ate,
        ultimo_nsu=empresa.ultimo_nsu,
        max_nsu=empresa.max_nsu_conhecido,
        ultima_consulta=empresa.ultima_consulta,
        proxima_consulta_periodica=empresa.proxima_consulta_periodica,
        modulo_nfse_ativo=bool(getattr(empresa, "modulo_nfse_ativo", False)),
        ultimo_nsu_nfse=getattr(empresa, "ultimo_nsu_nfse", None),
        max_nsu_nfse=getattr(empresa, "max_nsu_nfse", None),
        proxima_consulta_nfse=getattr(empresa, "proxima_consulta_nfse", None),
        ultima_nfe_qtd=getattr(empresa, "ultima_sync_nfe_qtd", None),
        ultima_nfse_qtd=getattr(empresa, "ultima_sync_nfse_qtd", None),
        ultimo_cstat=getattr(empresa, "ultima_sync_cstat", None),
        tem_certificado=bool(empresa.certificado_base64),
        certificado_valido_ate=getattr(empresa, "certificado_valido_ate", None),
    )
    return {
        "id": empresa.id,
        "razao_social": empresa.razao_social or "",
        "cnpj": empresa.cnpj or "",
        "cnpj_base": cnpj_base(empresa.cnpj),
        "ultimo_nsu": empresa.ultimo_nsu,
        "max_nsu_conhecido": empresa.max_nsu_conhecido,
        "modulo_nfse_ativo": bool(getattr(empresa, "modulo_nfse_ativo", False)),
        "sync_ativo": bool(sync_ativo),
        "agente_ativo": bool(agente_ativo),
        "pausado_em": _iso(pausado_em),
        "retomar_em": _iso(retomar_em),
        "agente_retomar_em": _iso(agente_retomar_em),
        "retomada_automatica": bool(retomada_automatica),
        "faixa_atencao": faixa or "",
        "ultima_sync_mensagem": empresa.ultima_sync_mensagem or "",
        "ultima_sync_nfse_mensagem": getattr(empresa, "ultima_sync_nfse_mensagem", None) or "",
        "proxima_consulta_periodica": _iso(empresa.proxima_consulta_periodica),
        "proxima_consulta_nfse": _iso(getattr(empresa, "proxima_consulta_nfse", None)),
        **painel,
    }


def _listar_resumos(db: Session, usuario: Usuario) -> tuple[list[dict], bool]:
    retomou = _aplicar_retomadas(db, usuario)
    empresas = _empresas_aprovadas(db, usuario)
    for empresa in empresas:
        _preencher_validade_certificado(db, empresa)
    flags = _garantir_flags_sync(db, usuario, [e.id for e in empresas])
    linhas = {
        linha.empresa_id: linha
        for linha in db.query(ContadorSyncEmpresa)
        .filter(
            ContadorSyncEmpresa.usuario_id == usuario.id,
            ContadorSyncEmpresa.empresa_id.in_([e.id for e in empresas] or [-1]),
        )
        .all()
    }
    agente = bool(getattr(usuario, "contador_agente_ativo", False))
    agora = datetime.now()
    parcial = []
    for empresa in empresas:
        linha = linhas.get(empresa.id)
        parcial.append(
            {
                "sync_ativo": flags.get(empresa.id, True),
                "retomar_em": linha.retomar_em if linha else None,
            }
        )
    faixa = faixa_atencao(
        agente_ativo=agente,
        agente_retomar_em=getattr(usuario, "contador_agente_retomar_em", None),
        empresas=parcial,
        agora=agora,
    )
    resumos = []
    for empresa in empresas:
        linha = linhas.get(empresa.id)
        resumos.append(
            _resumo(
                empresa,
                sync_ativo=flags.get(empresa.id, True),
                agente_ativo=agente,
                pausado_em=linha.pausado_em if linha else None,
                retomar_em=linha.retomar_em if linha else None,
                agente_retomar_em=getattr(usuario, "contador_agente_retomar_em", None),
                retomada_automatica=retomou,
                faixa=faixa,
                agora=agora,
            )
        )
    return resumos, retomou


def _exigir_contador(usuario: Usuario) -> None:
    if getattr(usuario, "role", None) != "contador":
        raise HTTPException(status_code=403, detail="Conta de contador necessária.")
    if (getattr(usuario, "status", None) or "ativo") != "ativo":
        raise HTTPException(status_code=403, detail="Conta não liberada. Fale com o suporte.")


def _empresa_do_contador(db: Session, usuario: Usuario, empresa_id: int) -> Empresa:
    _exigir_contador(usuario)
    if empresa_id not in ids_empresas_acessiveis(db, usuario):
        raise HTTPException(status_code=404, detail="Empresa não encontrada.")
    empresa = db.query(Empresa).filter_by(id=empresa_id).first()
    if not empresa:
        raise HTTPException(status_code=404, detail="Empresa não encontrada.")
    return empresa


def _empresas_aprovadas(db: Session, usuario: Usuario) -> list[Empresa]:
    ids = ids_empresas_acessiveis(db, usuario)
    if not ids:
        return []
    return (
        db.query(Empresa)
        .filter(Empresa.id.in_(ids), Empresa.status == "aprovado")
        .order_by(Empresa.razao_social.asc())
        .all()
    )


def _pode_consultar_agora(empresa: Empresa) -> dict:
    agora = datetime.now()
    painel = montar_painel(
        agora=agora,
        status=getattr(empresa, "status", None),
        bloqueio_656_ate=empresa.bloqueio_656_ate,
        bloqueio_manual_ate=empresa.bloqueio_manual_ate,
        ultimo_nsu=empresa.ultimo_nsu,
        max_nsu=empresa.max_nsu_conhecido,
        ultima_consulta=empresa.ultima_consulta,
        proxima_consulta_periodica=empresa.proxima_consulta_periodica,
        modulo_nfse_ativo=bool(getattr(empresa, "modulo_nfse_ativo", False)),
        ultimo_nsu_nfse=getattr(empresa, "ultimo_nsu_nfse", None),
        max_nsu_nfse=getattr(empresa, "max_nsu_nfse", None),
        proxima_consulta_nfse=getattr(empresa, "proxima_consulta_nfse", None),
        ultima_nfe_qtd=getattr(empresa, "ultima_sync_nfe_qtd", None),
        ultima_nfse_qtd=getattr(empresa, "ultima_sync_nfse_qtd", None),
        ultimo_cstat=getattr(empresa, "ultima_sync_cstat", None),
        tem_certificado=bool(empresa.certificado_base64),
        certificado_valido_ate=getattr(empresa, "certificado_valido_ate", None),
    )
    return painel


@router.post("/login")
async def contador_login(
    body: LoginContadorRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    ip = client_ip(request)
    email = body.email.strip().lower()
    limite = verificar_limite(db, "contador_login", ip, email)
    if limite:
        raise HTTPException(status_code=429, detail=limite)
    usuario = db.query(Usuario).filter_by(email=email).first()
    if not usuario or not verificar_senha(body.senha, usuario.senha_hash):
        registrar_tentativa(db, "contador_login", ip, email)
        raise HTTPException(status_code=401, detail="Email ou senha incorretos")
    if getattr(usuario, "role", None) != "contador":
        raise HTTPException(status_code=403, detail="Esta conta não é de contador.")
    if (getattr(usuario, "status", None) or "ativo") != "ativo":
        raise HTTPException(status_code=403, detail="Conta não liberada. Fale com o suporte.")
    resumos, _retomou = _listar_resumos(db, usuario)
    agente = bool(getattr(usuario, "contador_agente_ativo", False))
    limpar_sucesso(db, "contador_login", email=email)
    return {
        "token": criar_token(usuario.id),
        "agente_ativo": agente,
        "empresas": resumos,
    }


@router.get("/empresas")
async def contador_empresas(
    usuario: Usuario = Depends(get_usuario_atual),
    db: Session = Depends(get_db),
):
    _exigir_contador(usuario)
    resumos, _retomou = _listar_resumos(db, usuario)
    return resumos


@router.post("/sync/iniciar")
async def contador_sync_iniciar(
    usuario: Usuario = Depends(get_usuario_atual),
    db: Session = Depends(get_db),
):
    _exigir_contador(usuario)
    emp = db.query(Usuario).filter_by(id=usuario.id).first()
    emp.contador_agente_ativo = True
    emp.contador_agente_pausado_em = None
    emp.contador_agente_retomar_em = None
    empresas = _empresas_aprovadas(db, emp)
    _garantir_flags_sync(db, emp, [e.id for e in empresas])
    db.commit()
    return {"ok": True, "agente_ativo": True}


@router.post("/sync/parar")
async def contador_sync_parar(
    body: Optional[SyncPararBody] = None,
    usuario: Usuario = Depends(get_usuario_atual),
    db: Session = Depends(get_db),
):
    _exigir_contador(usuario)
    automatico = True if body is None else bool(body.retomar_automatico)
    agora = datetime.now()
    emp = db.query(Usuario).filter_by(id=usuario.id).first()
    emp.contador_agente_ativo = False
    emp.contador_agente_pausado_em = agora
    emp.contador_agente_retomar_em = momento_retomada(agora, automatico)
    db.commit()
    return {
        "ok": True,
        "agente_ativo": False,
        "agente_retomar_em": _iso(emp.contador_agente_retomar_em),
    }


@router.post("/empresas/{empresa_id}/sync")
async def contador_empresa_sync(
    empresa_id: int,
    body: EmpresaSyncBody,
    usuario: Usuario = Depends(get_usuario_atual),
    db: Session = Depends(get_db),
):
    empresa = _empresa_do_contador(db, usuario, empresa_id)
    row = (
        db.query(ContadorSyncEmpresa)
        .filter_by(usuario_id=usuario.id, empresa_id=empresa_id)
        .first()
    )
    agora = datetime.now()
    if row is None:
        row = ContadorSyncEmpresa(
            usuario_id=usuario.id,
            empresa_id=empresa_id,
            ativo=bool(body.ativo),
        )
        db.add(row)
    row.ativo = bool(body.ativo)
    row.updated_at = agora
    if body.ativo:
        row.pausado_em = None
        row.retomar_em = None
    else:
        row.pausado_em = agora
        row.retomar_em = momento_retomada(agora, body.retomar_automatico)
    db.commit()
    resumos, _retomou = _listar_resumos(db, usuario)
    for item in resumos:
        if item["id"] == empresa_id:
            return item
    return _resumo(
        empresa,
        sync_ativo=bool(body.ativo),
        agente_ativo=bool(getattr(usuario, "contador_agente_ativo", False)),
        pausado_em=row.pausado_em,
        retomar_em=row.retomar_em,
    )


@router.post("/heartbeat")
async def contador_heartbeat(
    body: HeartbeatContadorRequest,
    usuario: Usuario = Depends(get_usuario_atual),
    db: Session = Depends(get_db),
):
    empresa = _empresa_do_contador(db, usuario, body.empresa_id)
    empresa.contador_ultimo_heartbeat = datetime.now()
    if body.versao:
        empresa.contador_versao = body.versao[:20]
    db.commit()
    return {
        "status": "ok",
        "estado": estado_sync(empresa),
        "sync_ativo": _sync_ativo_empresa(db, usuario, empresa.id),
        "agente_ativo": bool(getattr(usuario, "contador_agente_ativo", False)),
    }


@router.post("/sincronizar/{empresa_id}")
async def contador_sincronizar(
    empresa_id: int,
    usuario: Usuario = Depends(get_usuario_atual),
    db: Session = Depends(get_db),
):
    """Pede um ciclo permitido. O servidor fala com a Receita e não zera o NSU."""
    _aplicar_retomadas(db, usuario)
    db.refresh(usuario)
    empresa = _empresa_do_contador(db, usuario, empresa_id)
    if empresa.status != "aprovado":
        raise HTTPException(status_code=403, detail="Empresa ainda não aprovada.")

    agente = bool(getattr(usuario, "contador_agente_ativo", False))
    sync_ok = _sync_ativo_empresa(db, usuario, empresa_id)
    linha = _sync_row(db, usuario, empresa_id)

    def _fechar(executado: bool, motivo: str | None = None):
        atual = db.query(Empresa).filter_by(id=empresa_id).first() or empresa
        resumo = _resumo(
            atual,
            sync_ativo=sync_ok,
            agente_ativo=agente,
            pausado_em=linha.pausado_em if linha else None,
            retomar_em=linha.retomar_em if linha else None,
            agente_retomar_em=getattr(usuario, "contador_agente_retomar_em", None),
        )
        resumo["ok"] = True
        resumo["executado"] = executado
        if motivo:
            resumo["motivo"] = motivo
        return resumo

    if not agente or not sync_ok:
        return _fechar(
            False,
            "Buscas paradas no agente" if not agente else "Sync pausado para esta empresa",
        )

    agenda = _pode_consultar_agora(empresa)
    if not agenda["pode_consultar"]:
        return _fechar(False)

    if agenda["pode_consultar_nfe"]:
        await asyncio.to_thread(worker.executar_consulta, empresa.id)
        db.expire_all()
    return _fechar(True)
