from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from app.models.database import Etiqueta, NotaEtiqueta, NotaFiscal
from app.core.dependencies import get_db, get_usuario_atual, get_empresa_ativa
from datetime import datetime

router = APIRouter()

# Schemas Pydantic
class EtiquetaCreate(BaseModel):
    nome: str
    cor: str = "#3B82F6"  # borda
    cor_fundo: Optional[str] = None
    cor_texto: Optional[str] = None
    empresa_id: Optional[int] = None

class EtiquetaResponse(BaseModel):
    id: int
    nome: str
    cor: str
    cor_fundo: Optional[str] = None
    cor_texto: Optional[str] = None
    empresa_id: Optional[int]
    created_at: datetime
    
    class Config:
        from_attributes = True

class EtiquetaLoteRequest(BaseModel):
    nota_ids: List[int]
    etiqueta_id: Optional[int] = None
    etiqueta_ids: Optional[List[int]] = None

@router.get("/", response_model=List[EtiquetaResponse])
async def listar_etiquetas(
    usuario = Depends(get_usuario_atual),
    empresa = Depends(get_empresa_ativa),
    db: Session = Depends(get_db)
):
    """Lista todas as etiquetas do usuário (compartilhadas entre empresas)."""
    etiquetas = db.query(Etiqueta).filter(
        Etiqueta.usuario_id == usuario.id
    ).all()
    return etiquetas

@router.post("/", response_model=EtiquetaResponse, status_code=status.HTTP_201_CREATED)
async def criar_etiqueta(
    etiqueta_data: EtiquetaCreate,
    usuario = Depends(get_usuario_atual),
    empresa = Depends(get_empresa_ativa),
    db: Session = Depends(get_db)
):
    """Cria uma nova etiqueta."""
    def _hex_ok(c):
        return c and c.startswith("#") and len(c) == 7

    if not _hex_ok(etiqueta_data.cor):
        raise HTTPException(status_code=400, detail="Cor (borda) deve estar no formato #RRGGBB")
    if etiqueta_data.cor_fundo and not _hex_ok(etiqueta_data.cor_fundo):
        raise HTTPException(status_code=400, detail="Cor de fundo deve estar no formato #RRGGBB")
    if etiqueta_data.cor_texto and not _hex_ok(etiqueta_data.cor_texto):
        raise HTTPException(status_code=400, detail="Cor do texto deve estar no formato #RRGGBB")

    cor_borda = etiqueta_data.cor
    cor_fundo = etiqueta_data.cor_fundo or cor_borda
    cor_texto = etiqueta_data.cor_texto or cor_borda

    nova_etiqueta = Etiqueta(
        usuario_id=usuario.id,
        empresa_id=None,
        nome=etiqueta_data.nome,
        cor=cor_borda,
        cor_fundo=cor_fundo,
        cor_texto=cor_texto,
        created_at=datetime.now()
    )
    
    db.add(nova_etiqueta)
    db.commit()
    db.refresh(nova_etiqueta)
    
    return nova_etiqueta

@router.put("/{etiqueta_id}", response_model=EtiquetaResponse)
async def editar_etiqueta(
    etiqueta_id: int,
    etiqueta_data: EtiquetaCreate,
    usuario = Depends(get_usuario_atual),
    db: Session = Depends(get_db)
):
    """Edita uma etiqueta existente."""
    etiqueta = db.query(Etiqueta).filter(
        Etiqueta.id == etiqueta_id,
        Etiqueta.usuario_id == usuario.id
    ).first()
    
    if not etiqueta:
        raise HTTPException(status_code=404, detail="Etiqueta não encontrada")
    
    def _hex_ok(c):
        return c and c.startswith("#") and len(c) == 7

    if not _hex_ok(etiqueta_data.cor):
        raise HTTPException(status_code=400, detail="Cor (borda) deve estar no formato #RRGGBB")
    if etiqueta_data.cor_fundo and not _hex_ok(etiqueta_data.cor_fundo):
        raise HTTPException(status_code=400, detail="Cor de fundo deve estar no formato #RRGGBB")
    if etiqueta_data.cor_texto and not _hex_ok(etiqueta_data.cor_texto):
        raise HTTPException(status_code=400, detail="Cor do texto deve estar no formato #RRGGBB")

    etiqueta.nome = etiqueta_data.nome
    etiqueta.cor = etiqueta_data.cor
    etiqueta.cor_fundo = etiqueta_data.cor_fundo or etiqueta_data.cor
    etiqueta.cor_texto = etiqueta_data.cor_texto or etiqueta_data.cor

    db.commit()
    db.refresh(etiqueta)
    
    return etiqueta

@router.delete("/{etiqueta_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deletar_etiqueta(
    etiqueta_id: int,
    usuario = Depends(get_usuario_atual),
    db: Session = Depends(get_db)
):
    """Deleta uma etiqueta e remove todas as associações."""
    etiqueta = db.query(Etiqueta).filter(
        Etiqueta.id == etiqueta_id,
        Etiqueta.usuario_id == usuario.id
    ).first()
    
    if not etiqueta:
        raise HTTPException(status_code=404, detail="Etiqueta não encontrada")
    
    # Remove todas as associações
    db.query(NotaEtiqueta).filter(NotaEtiqueta.etiqueta_id == etiqueta_id).delete()

    # Remove a etiqueta
    db.delete(etiqueta)
    db.commit()
    
    return None

@router.post("/notas/{nota_id}/etiquetas/{etiqueta_id}", status_code=status.HTTP_201_CREATED)
async def aplicar_etiqueta(
    nota_id: int,
    etiqueta_id: int,
    usuario = Depends(get_usuario_atual),
    empresa = Depends(get_empresa_ativa),
    db: Session = Depends(get_db)
):
    """Aplica uma etiqueta a uma nota."""
    # Verifica se a nota existe e pertence à empresa
    nota = db.query(NotaFiscal).filter(
        NotaFiscal.id == nota_id,
        NotaFiscal.empresa_id == empresa.id
    ).first()
    
    if not nota:
        raise HTTPException(status_code=404, detail="Nota não encontrada")
    
    # Verifica se a etiqueta existe e pertence ao usuário
    etiqueta = db.query(Etiqueta).filter(
        Etiqueta.id == etiqueta_id,
        Etiqueta.usuario_id == usuario.id
    ).first()
    
    if not etiqueta:
        raise HTTPException(status_code=404, detail="Etiqueta não encontrada")
    
    # Verifica se já não está aplicada
    existe = db.query(NotaEtiqueta).filter(
        NotaEtiqueta.nota_id == nota_id,
        NotaEtiqueta.etiqueta_id == etiqueta_id
    ).first()
    
    if existe:
        return {"message": "Etiqueta já aplicada"}
    
    # Aplica a etiqueta
    nota_etiqueta = NotaEtiqueta(
        nota_id=nota_id,
        etiqueta_id=etiqueta_id,
        created_at=datetime.now()
    )
    
    db.add(nota_etiqueta)
    db.commit()
    
    return {"message": "Etiqueta aplicada com sucesso"}

@router.delete("/notas/{nota_id}/etiquetas/{etiqueta_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remover_etiqueta(
    nota_id: int,
    etiqueta_id: int,
    usuario = Depends(get_usuario_atual),
    empresa = Depends(get_empresa_ativa),
    db: Session = Depends(get_db)
):
    """Remove uma etiqueta de uma nota."""
    # Verifica se a nota existe e pertence à empresa
    nota = db.query(NotaFiscal).filter(
        NotaFiscal.id == nota_id,
        NotaFiscal.empresa_id == empresa.id
    ).first()
    
    if not nota:
        raise HTTPException(status_code=404, detail="Nota não encontrada")
    
    # Remove a associação
    nota_etiqueta = db.query(NotaEtiqueta).filter(
        NotaEtiqueta.nota_id == nota_id,
        NotaEtiqueta.etiqueta_id == etiqueta_id
    ).first()
    
    if not nota_etiqueta:
        raise HTTPException(status_code=404, detail="Etiqueta não encontrada nesta nota")
    
    db.delete(nota_etiqueta)
    db.commit()
    
    return None

@router.get("/notas/{nota_id}/etiquetas", response_model=List[EtiquetaResponse])
async def listar_etiquetas_nota(
    nota_id: int,
    usuario = Depends(get_usuario_atual),
    empresa = Depends(get_empresa_ativa),
    db: Session = Depends(get_db)
):
    """Lista todas as etiquetas de uma nota."""
    # Verifica se a nota existe e pertence à empresa
    nota = db.query(NotaFiscal).filter(
        NotaFiscal.id == nota_id,
        NotaFiscal.empresa_id == empresa.id
    ).first()
    
    if not nota:
        raise HTTPException(status_code=404, detail="Nota não encontrada")
    
    # Busca etiquetas da nota
    nota_etiquetas = db.query(NotaEtiqueta).filter(NotaEtiqueta.nota_id == nota_id).all()
    etiqueta_ids = [ne.etiqueta_id for ne in nota_etiquetas]
    
    if not etiqueta_ids:
        return []
    
    etiquetas = db.query(Etiqueta).filter(Etiqueta.id.in_(etiqueta_ids)).all()
    return etiquetas

@router.post("/notas/etiquetas-lote")
async def aplicar_etiqueta_lote(
    payload: EtiquetaLoteRequest,
    usuario = Depends(get_usuario_atual),
    empresa = Depends(get_empresa_ativa),
    db: Session = Depends(get_db)
):
    """Aplica uma etiqueta em lote (adição) às notas selecionadas."""
    if not payload.nota_ids:
        raise HTTPException(status_code=400, detail="Nenhuma nota selecionada")

    etiqueta_ids = payload.etiqueta_ids or ([payload.etiqueta_id] if payload.etiqueta_id else [])
    etiqueta_ids = [int(eid) for eid in etiqueta_ids if eid]
    if not etiqueta_ids:
        raise HTTPException(status_code=400, detail="Nenhuma etiqueta selecionada")

    etiquetas = db.query(Etiqueta).filter(
        Etiqueta.id.in_(etiqueta_ids),
        Etiqueta.usuario_id == usuario.id
    ).all()
    if len(etiquetas) != len(set(etiqueta_ids)):
        raise HTTPException(status_code=404, detail="Uma ou mais etiquetas não encontradas")

    notas = db.query(NotaFiscal).filter(
        NotaFiscal.id.in_(payload.nota_ids),
        NotaFiscal.empresa_id == empresa.id
    ).all()
    if not notas:
        raise HTTPException(status_code=404, detail="Notas não encontradas")

    existentes = db.query(NotaEtiqueta.nota_id, NotaEtiqueta.etiqueta_id).filter(
        NotaEtiqueta.nota_id.in_(payload.nota_ids),
        NotaEtiqueta.etiqueta_id.in_(etiqueta_ids)
    ).all()
    existentes_set = {(n_id, e_id) for n_id, e_id in existentes}

    aplicadas = 0
    for nota in notas:
        for etiqueta in etiquetas:
            if (nota.id, etiqueta.id) in existentes_set:
                continue
            db.add(NotaEtiqueta(
                nota_id=nota.id,
                etiqueta_id=etiqueta.id,
                created_at=datetime.now()
            ))
            aplicadas += 1

    db.commit()
    return {"aplicadas": aplicadas}

