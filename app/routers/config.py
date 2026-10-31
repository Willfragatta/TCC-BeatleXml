"""
Rotas para página de configurações.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from app.models.database import Empresa, Usuario
from app.core.dependencies import (
    get_db,
    get_usuario_atual,
    get_empresa_ativa,
    papel_na_empresa,
    vinculo_empresa,
)
from app.core.auth import hash_senha, verificar_senha
from app.core.templates import templates
from app.services.exclusao import apagar_conta, limpar_certificado

router = APIRouter()


def _usuario_na_sessao(db: Session, usuario: Usuario) -> Usuario:
    """O middleware entrega o usuário de outra sessão. A gravação precisa desta."""
    atual = db.get(Usuario, usuario.id)
    if atual is None:
        raise HTTPException(status_code=401, detail="Usuário não autenticado")
    return atual

class AlterarSenhaRequest(BaseModel):
    senha_atual: str
    nova_senha: str

class ConfigEmpresaRequest(BaseModel):
    ambiente: Optional[str] = None
    uf_autor: Optional[str] = None
    pasta_destino: Optional[str] = None

def _empresa_ou_nenhuma(request: Request, usuario: Usuario, db: Session):
    try:
        return get_empresa_ativa(request, usuario, db)
    except HTTPException:
        return None


def _master_da_empresa(db: Session, usuario: Usuario, empresa: Empresa) -> bool:
    """Dono ou vínculo empresa_master. O atalho de administrador não conta."""
    if empresa.usuario_id == usuario.id:
        return True
    vinculo = vinculo_empresa(db, usuario, empresa.id)
    return bool(vinculo and vinculo.papel == "empresa_master")


def _pode_apagar_conta(db: Session, usuario: Usuario, empresa) -> bool:
    if empresa is None:
        return True
    if _master_da_empresa(db, usuario, empresa):
        return True
    return papel_na_empresa(db, usuario, empresa) != "leitura"


@router.get("/")
async def pagina_configuracoes(
    request: Request,
    usuario: Usuario = Depends(get_usuario_atual),
    db: Session = Depends(get_db)
):
    """Página de configurações."""
    empresa = _empresa_ou_nenhuma(request, usuario, db)
    return templates.TemplateResponse(
        request,
        "config/index.html",
        {
            "usuario": usuario,
            "empresa": empresa,
            "pode_remover_certificado": bool(empresa and _master_da_empresa(db, usuario, empresa)),
            "pode_apagar_conta": _pode_apagar_conta(db, usuario, empresa),
        },
    )

@router.post("/alterar-senha")
async def alterar_senha(
    request: AlterarSenhaRequest,
    usuario: Usuario = Depends(get_usuario_atual),
    db: Session = Depends(get_db)
):
    """Altera a senha do usuário."""
    atual = _usuario_na_sessao(db, usuario)
    if not verificar_senha(request.senha_atual, atual.senha_hash):
        raise HTTPException(status_code=400, detail="Senha atual incorreta")

    if len(request.nova_senha) < 6:
        raise HTTPException(status_code=400, detail="Nova senha deve ter no mínimo 6 caracteres")

    atual.senha_hash = hash_senha(request.nova_senha)
    db.commit()
    
    return {"message": "Senha alterada com sucesso"}


class DoisFatoresRequest(BaseModel):
    ligado: bool
    senha_atual: str


@router.post("/dois-fatores")
async def configurar_dois_fatores(
    payload: DoisFatoresRequest,
    usuario: Usuario = Depends(get_usuario_atual),
    db: Session = Depends(get_db),
):
    atual = _usuario_na_sessao(db, usuario)
    if not verificar_senha(payload.senha_atual, atual.senha_hash):
        raise HTTPException(status_code=400, detail="Senha atual incorreta")
    atual.dois_fatores_email = bool(payload.ligado)
    db.commit()
    if payload.ligado:
        return {"message": "2FA ligado. No próximo login enviaremos um código por e-mail."}
    return {"message": "2FA desligado."}

@router.post("/config-empresa")
async def configurar_empresa(
    request: ConfigEmpresaRequest,
    empresa: Empresa = Depends(get_empresa_ativa),
    db: Session = Depends(get_db)
):
    """Atualiza configurações da empresa."""
    if request.ambiente:
        empresa.ambiente = request.ambiente
    if request.uf_autor:
        empresa.uf_autor = request.uf_autor
    if request.pasta_destino is not None:
        empresa.pasta_destino = request.pasta_destino

    db.commit()
    return {"message": "Configurações atualizadas com sucesso"}


def _limpar_sessao(resposta: JSONResponse) -> JSONResponse:
    resposta.delete_cookie("access_token")
    resposta.delete_cookie("pending_2fa")
    resposta.delete_cookie("empresa_ativa")
    return resposta


@router.post("/remover-certificado")
async def remover_certificado(
    request: Request,
    usuario: Usuario = Depends(get_usuario_atual),
    db: Session = Depends(get_db),
):
    """Tira o certificado A1 da empresa ativa. Só o master daquela empresa."""
    atual = _usuario_na_sessao(db, usuario)
    empresa = _empresa_ou_nenhuma(request, atual, db)
    if empresa is None:
        raise HTTPException(status_code=404, detail="Nenhuma empresa ativa")
    if not _master_da_empresa(db, atual, empresa):
        raise HTTPException(status_code=403, detail="Só o master da empresa remove o certificado")
    limpar_certificado(db, empresa)
    return {"message": "Certificado removido desta empresa"}


class ApagarContaRequest(BaseModel):
    senha_atual: str
    email: str


@router.post("/apagar-conta")
async def apagar_minha_conta(
    payload: ApagarContaRequest,
    request: Request,
    usuario: Usuario = Depends(get_usuario_atual),
    db: Session = Depends(get_db),
):
    """Apaga a conta. Dono leva as empresas. Vínculo leva só a conta."""
    atual = _usuario_na_sessao(db, usuario)
    empresa = _empresa_ou_nenhuma(request, atual, db)
    if not _pode_apagar_conta(db, atual, empresa):
        raise HTTPException(status_code=403, detail="Este papel não apaga a conta por aqui")
    if not verificar_senha(payload.senha_atual, atual.senha_hash):
        raise HTTPException(status_code=400, detail="Senha atual incorreta")
    informado = (payload.email or "").strip().lower()
    if informado != (atual.email or "").strip().lower():
        raise HTTPException(status_code=400, detail="Digite o e-mail da conta para confirmar")
    apagar_conta(db, atual)
    return _limpar_sessao(JSONResponse({"message": "Conta apagada"}))

