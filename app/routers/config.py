"""
Rotas para página de configurações.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from app.models.database import Empresa, Usuario
from app.core.dependencies import get_db, get_usuario_atual, get_empresa_ativa
from app.core.auth import hash_senha, verificar_senha
from app.core.templates import templates

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

@router.get("/")
async def pagina_configuracoes(
    request: Request,
    usuario: Usuario = Depends(get_usuario_atual),
    empresa: Empresa = Depends(get_empresa_ativa),
    db: Session = Depends(get_db)
):
    """Página de configurações."""
    return templates.TemplateResponse(
        request,
        "config/index.html",
        {"usuario": usuario, "empresa": empresa},
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

