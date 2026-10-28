"""
Rotas administrativas para gestão de empresas e usuários.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import logging
from app.models.database import Empresa, Usuario, NotaFiscal
from app.core.dependencies import get_db, get_usuario_atual

router = APIRouter()
logger = logging.getLogger("beatle_xml")

def verificar_admin(usuario: Usuario = Depends(get_usuario_atual)):
    """Verifica se o usuário é administrador."""
    if not usuario.is_admin:
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas administradores.")
    return usuario

class AprovarEmpresaRequest(BaseModel):
    empresa_id: int
    acao: str  # 'aprovar' ou 'rejeitar'
    motivo: Optional[str] = None

@router.get("/empresas")
async def listar_empresas_admin(
    admin: Usuario = Depends(verificar_admin),
    db: Session = Depends(get_db)
):
    """Lista todas as empresas para o admin."""
    empresas = db.query(Empresa).all()
    
    resultado = []
    for empresa in empresas:
        total_notas = db.query(NotaFiscal).filter_by(empresa_id=empresa.id).count()
        usuario = db.query(Usuario).filter_by(id=empresa.usuario_id).first()
        
        resultado.append({
            "id": empresa.id,
            "cnpj": empresa.cnpj,
            "razao_social": empresa.razao_social,
            "status": empresa.status,
            "usuario_email": usuario.email if usuario else "N/A",
            "usuario_nome": usuario.nome_completo if usuario else "N/A",
            "total_notas": total_notas,
            "ultima_consulta": empresa.ultima_consulta.isoformat() if empresa.ultima_consulta else None,
            "data_aprovacao": empresa.data_aprovacao.isoformat() if empresa.data_aprovacao else None,
            "data_ativacao": empresa.data_ativacao.isoformat() if empresa.data_ativacao else None,
            "bloqueio_656_ate": empresa.bloqueio_656_ate.isoformat() if empresa.bloqueio_656_ate else None
        })
    
    return resultado

@router.post("/empresas/aprovar")
async def aprovar_empresa(
    request: AprovarEmpresaRequest,
    admin: Usuario = Depends(verificar_admin),
    db: Session = Depends(get_db)
):
    """Aprova ou rejeita uma empresa."""
    empresa = db.query(Empresa).filter_by(id=request.empresa_id).first()
    if not empresa:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    
    if request.acao == 'aprovar':
        empresa.status = 'aprovado'
        empresa.data_aprovacao = datetime.now()
        empresa.data_ativacao = datetime.now()  # Ativa por 1 mês
        
        # Inicializa max_nsu_conhecido se não tiver
        if not empresa.max_nsu_conhecido or empresa.max_nsu_conhecido == '000000000000000':
            # Se ultimo_nsu tem valor válido, usa ele como max_nsu_conhecido
            if empresa.ultimo_nsu and empresa.ultimo_nsu != '0' and empresa.ultimo_nsu != '000000000000000':
                empresa.max_nsu_conhecido = empresa.ultimo_nsu.zfill(15)
                logger.info(f"max_nsu_conhecido inicializado com ultimo_nsu para empresa {empresa.id}")
            else:
                # Inicializa com zeros
                empresa.max_nsu_conhecido = '000000000000000'
                logger.info(f"max_nsu_conhecido inicializado com zeros para empresa {empresa.id}")
        
        # Garante que ultimo_nsu tem 15 dígitos
        if empresa.ultimo_nsu and len(empresa.ultimo_nsu) < 15:
            empresa.ultimo_nsu = empresa.ultimo_nsu.zfill(15)
        
        db.commit()
        return {"message": "Empresa aprovada com sucesso", "status": "aprovado"}
    elif request.acao == 'rejeitar':
        # Remove empresa do banco (conforme especificação do William)
        db.delete(empresa)
        db.commit()
        return {"message": "Empresa rejeitada e removida do sistema", "status": "rejeitado"}
    else:
        raise HTTPException(status_code=400, detail="Ação inválida. Use 'aprovar' ou 'rejeitar'")

@router.get("/empresas/pendentes")
async def contar_empresas_pendentes(
    admin: Usuario = Depends(verificar_admin),
    db: Session = Depends(get_db)
):
    """Conta empresas pendentes de aprovação."""
    count = db.query(Empresa).filter_by(status='pendente_aprovacao').count()
    return {"pendentes": count}

@router.get("/usuarios")
async def listar_usuarios(
    admin: Usuario = Depends(verificar_admin),
    db: Session = Depends(get_db)
):
    """Lista todos os usuários."""
    usuarios = db.query(Usuario).all()
    
    resultado = []
    for usuario in usuarios:
        empresas = db.query(Empresa).filter_by(usuario_id=usuario.id).all()
        
        resultado.append({
            "id": usuario.id,
            "email": usuario.email,
            "nome_completo": usuario.nome_completo,
            "telefone": usuario.telefone,
            "status": usuario.status,
            "is_admin": usuario.is_admin,
            "total_empresas": len(empresas),
            "created_at": usuario.created_at.isoformat() if usuario.created_at else None,
            "last_login": usuario.last_login.isoformat() if usuario.last_login else None
        })
    
    return resultado

