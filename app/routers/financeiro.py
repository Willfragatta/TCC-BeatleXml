"""
Router do módulo Financeiro (A Pagar): listagem de parcelas, totais e marcar como pago.
"""
import csv
import io
from fastapi import APIRouter, Depends, HTTPException, Query, Body
from fastapi.responses import Response
from sqlalchemy.orm import Session
from sqlalchemy import or_, asc, desc
from typing import Optional, List
from datetime import datetime, timedelta
from pydantic import BaseModel

from app.models.database import NotaFiscal, ParcelaPagamento
from app.core.dependencies import get_db, get_usuario_atual, get_empresa_ativa

router = APIRouter()


def _status_parcela(data_vencimento, data_pagamento):
    hoje = datetime.now().date()
    if data_pagamento:
        return "pago"
    if data_vencimento:
        venc = data_vencimento.date() if hasattr(data_vencimento, "date") else data_vencimento
        if venc < hoje:
            return "vencido"
    return "pendente"


@router.get("/parcelas")
async def listar_parcelas(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    busca: Optional[str] = Query(None, description="Busca por emitente, número da nota, chave"),
    data_vencimento_inicio: Optional[str] = None,
    data_vencimento_fim: Optional[str] = None,
    forma_pagamento: Optional[str] = None,
    status: Optional[str] = Query(None, description="pendente | pago | vencido | nao_pagos"),
    order_by: Optional[str] = Query("data_vencimento", description="data_vencimento, valor, emitente_nome"),
    order_dir: Optional[str] = Query("asc", description="asc | desc"),
    usuario = Depends(get_usuario_atual),
    empresa = Depends(get_empresa_ativa),
    db: Session = Depends(get_db),
):
    """Lista parcelas da empresa com filtros e totais."""
    hoje = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    inicio_mes = hoje.replace(day=1)

    base = (
        db.query(ParcelaPagamento)
        .join(NotaFiscal, ParcelaPagamento.nota_id == NotaFiscal.id)
        .filter(NotaFiscal.empresa_id == empresa.id)
    )

    if busca and busca.strip():
        termo = f"%{busca.strip()}%"
        base = base.filter(
            or_(
                NotaFiscal.emitente_nome.ilike(termo),
                NotaFiscal.chave_acesso.ilike(termo),
                NotaFiscal.numero_nfe.ilike(termo),
            )
        )

    if data_vencimento_inicio:
        try:
            d_ini = datetime.strptime(data_vencimento_inicio, "%Y-%m-%d")
            base = base.filter(ParcelaPagamento.data_vencimento >= d_ini)
        except ValueError:
            pass
    if data_vencimento_fim:
        try:
            d_fim = datetime.strptime(data_vencimento_fim, "%Y-%m-%d") + timedelta(days=1)
            base = base.filter(ParcelaPagamento.data_vencimento < d_fim)
        except ValueError:
            pass
    if forma_pagamento:
        base = base.filter(
            or_(
                ParcelaPagamento.forma_pagamento == forma_pagamento,
                ParcelaPagamento.forma_pagamento_label.ilike(f"%{forma_pagamento}%"),
            )
        )

    # Totais (antes de filtrar por status)
    todas = base.with_entities(
        ParcelaPagamento.id,
        ParcelaPagamento.data_vencimento,
        ParcelaPagamento.data_pagamento,
        ParcelaPagamento.valor,
    ).all()
    a_vencer_hoje = sum(
        float(r[3]) for r in todas
        if r[2] is None and r[1] and (r[1].date() if hasattr(r[1], "date") else r[1]) == hoje.date()
    )
    vencido_total = sum(
        float(r[3]) for r in todas
        if r[2] is None and r[1] and (r[1].date() if hasattr(r[1], "date") else r[1]) < hoje.date()
    )
    pago_mes = sum(
        float(r[3]) for r in todas
        if r[2] and r[2] >= inicio_mes
    )

    # Filtro por status: nao_pagos = data_pagamento IS NULL; demais em Python
    if status:
        if status == "nao_pagos":
            base = base.filter(ParcelaPagamento.data_pagamento.is_(None))
        else:
            base_ids = [r[0] for r in base.with_entities(ParcelaPagamento.id).all()]
            parcelas_status = (
                db.query(ParcelaPagamento)
                .join(NotaFiscal, ParcelaPagamento.nota_id == NotaFiscal.id)
                .filter(NotaFiscal.empresa_id == empresa.id, ParcelaPagamento.id.in_(base_ids))
            )
            rows = parcelas_status.all()
            filtered_ids = []
            for p in rows:
                st = _status_parcela(p.data_vencimento, p.data_pagamento)
                if st == status:
                    filtered_ids.append(p.id)
            if not filtered_ids:
                base = base.filter(ParcelaPagamento.id == -1)  # nenhum resultado
            else:
                base = base.filter(ParcelaPagamento.id.in_(filtered_ids))

    total_count = base.count()
    if total_count == 0:
        return {
            "parcelas": [],
            "total": 0,
            "skip": skip,
            "limit": limit,
            "totais": {
                "a_vencer_hoje": round(a_vencer_hoje, 2),
                "vencido": round(vencido_total, 2),
                "pago_mes": round(pago_mes, 2),
            },
        }
    order_col = {
        "data_vencimento": ParcelaPagamento.data_vencimento,
        "valor": ParcelaPagamento.valor,
        "emitente_nome": NotaFiscal.emitente_nome,
    }.get(order_by, ParcelaPagamento.data_vencimento)
    if order_dir == "desc":
        base = base.order_by(desc(order_col))
    else:
        base = base.order_by(asc(order_col))
    parcelas = base.offset(skip).limit(limit).all()

    items = []
    for p in parcelas:
        nota = db.query(NotaFiscal).filter_by(id=p.nota_id).first()
        st = _status_parcela(p.data_vencimento, p.data_pagamento)
        items.append({
            "id": p.id,
            "nota_id": p.nota_id,
            "numero_nfe": nota.numero_nfe if nota else None,
            "emitente_nome": nota.emitente_nome if nota else None,
            "chave_acesso": nota.chave_acesso if nota else None,
            "numero_parcela": p.numero_parcela,
            "data_vencimento": p.data_vencimento.isoformat() if p.data_vencimento else None,
            "valor": float(p.valor) if p.valor is not None else 0,
            "forma_pagamento": p.forma_pagamento,
            "forma_pagamento_label": p.forma_pagamento_label or "Outros",
            "status": st,
            "data_pagamento": p.data_pagamento.isoformat() if p.data_pagamento else None,
            "editavel": bool(p.editavel),
        })

    return {
        "parcelas": items,
        "total": total_count,
        "skip": skip,
        "limit": limit,
        "totais": {
            "a_vencer_hoje": round(a_vencer_hoje, 2),
            "vencido": round(vencido_total, 2),
            "pago_mes": round(pago_mes, 2),
        },
    }


class MarcarPagoBody(BaseModel):
    data_pagamento: Optional[str] = None  # YYYY-MM-DD; se omitido, usa data atual
    marcar_nao_pago: Optional[bool] = None  # True = limpar data_pagamento


@router.patch("/parcelas/{parcela_id}")
async def marcar_parcela_pago(
    parcela_id: int,
    body: Optional[MarcarPagoBody] = Body(None),
    usuario = Depends(get_usuario_atual),
    empresa = Depends(get_empresa_ativa),
    db: Session = Depends(get_db),
):
    """Marca parcela como paga ou como não paga. Só permitido quando editavel=True."""
    parcela = (
        db.query(ParcelaPagamento)
        .join(NotaFiscal, ParcelaPagamento.nota_id == NotaFiscal.id)
        .filter(
            ParcelaPagamento.id == parcela_id,
            NotaFiscal.empresa_id == empresa.id,
        )
    ).first()
    if not parcela:
        raise HTTPException(status_code=404, detail="Parcela não encontrada")
    if not parcela.editavel:
        raise HTTPException(status_code=403, detail="Esta parcela não pode ser alterada (dados da NFe)")

    if body and body.marcar_nao_pago is True:
        parcela.data_pagamento = None
        db.commit()
        db.refresh(parcela)
        return {
            "id": parcela.id,
            "data_pagamento": None,
            "status": _status_parcela(parcela.data_vencimento, parcela.data_pagamento),
        }
    # Marcar como pago
    if body and body.data_pagamento:
        try:
            data_pag = datetime.strptime(body.data_pagamento, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="data_pagamento deve ser YYYY-MM-DD")
    else:
        data_pag = datetime.now()
    parcela.data_pagamento = data_pag
    db.commit()
    db.refresh(parcela)
    return {
        "id": parcela.id,
        "data_pagamento": parcela.data_pagamento.isoformat() if parcela.data_pagamento else None,
        "status": "pago",
    }


class BulkMarcarPagoBody(BaseModel):
    ids: List[int]
    data_pagamento: Optional[str] = None  # YYYY-MM-DD; se omitido, usa data atual


class BulkMarcarNaoPagoBody(BaseModel):
    ids: List[int]


@router.post("/parcelas/marcar-pago")
async def marcar_parcelas_pago_lote(
    body: BulkMarcarPagoBody,
    usuario = Depends(get_usuario_atual),
    empresa = Depends(get_empresa_ativa),
    db: Session = Depends(get_db),
):
    """Marca várias parcelas como pagas. Apenas parcelas editáveis e da empresa."""
    if not body.ids:
        return {"marcados": 0, "ids": [], "erros": []}
    data_pag = datetime.now()
    if body.data_pagamento:
        try:
            data_pag = datetime.strptime(body.data_pagamento, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="data_pagamento deve ser YYYY-MM-DD")
    parcelas = (
        db.query(ParcelaPagamento)
        .join(NotaFiscal, ParcelaPagamento.nota_id == NotaFiscal.id)
        .filter(
            ParcelaPagamento.id.in_(body.ids),
            NotaFiscal.empresa_id == empresa.id,
            ParcelaPagamento.editavel == True,
        )
    ).all()
    for p in parcelas:
        p.data_pagamento = data_pag
    db.commit()
    return {"marcados": len(parcelas), "ids": [p.id for p in parcelas]}


@router.post("/parcelas/marcar-nao-pago")
async def marcar_parcelas_nao_pago_lote(
    body: BulkMarcarNaoPagoBody,
    usuario = Depends(get_usuario_atual),
    empresa = Depends(get_empresa_ativa),
    db: Session = Depends(get_db),
):
    """Marca várias parcelas como não pagas. Apenas parcelas editáveis e da empresa."""
    if not body.ids:
        return {"marcados": 0, "ids": []}
    parcelas = (
        db.query(ParcelaPagamento)
        .join(NotaFiscal, ParcelaPagamento.nota_id == NotaFiscal.id)
        .filter(
            ParcelaPagamento.id.in_(body.ids),
            NotaFiscal.empresa_id == empresa.id,
            ParcelaPagamento.editavel == True,
        )
    ).all()
    for p in parcelas:
        p.data_pagamento = None
    db.commit()
    return {"marcados": len(parcelas), "ids": [p.id for p in parcelas]}


@router.get("/exportar/excel")
async def exportar_financeiro_excel(
    busca: Optional[str] = None,
    data_vencimento_inicio: Optional[str] = None,
    data_vencimento_fim: Optional[str] = None,
    forma_pagamento: Optional[str] = None,
    status: Optional[str] = None,
    usuario = Depends(get_usuario_atual),
    empresa = Depends(get_empresa_ativa),
    db: Session = Depends(get_db),
):
    """Exporta parcelas (financeiro) para CSV/Excel com os mesmos filtros da listagem."""
    base = (
        db.query(ParcelaPagamento)
        .join(NotaFiscal, ParcelaPagamento.nota_id == NotaFiscal.id)
        .filter(NotaFiscal.empresa_id == empresa.id)
    )
    if busca and busca.strip():
        termo = f"%{busca.strip()}%"
        base = base.filter(
            or_(
                NotaFiscal.emitente_nome.ilike(termo),
                NotaFiscal.chave_acesso.ilike(termo),
                NotaFiscal.numero_nfe.ilike(termo),
            )
        )
    if data_vencimento_inicio:
        try:
            d_ini = datetime.strptime(data_vencimento_inicio, "%Y-%m-%d")
            base = base.filter(ParcelaPagamento.data_vencimento >= d_ini)
        except ValueError:
            pass
    if data_vencimento_fim:
        try:
            d_fim = datetime.strptime(data_vencimento_fim, "%Y-%m-%d") + timedelta(days=1)
            base = base.filter(ParcelaPagamento.data_vencimento < d_fim)
        except ValueError:
            pass
    if forma_pagamento:
        base = base.filter(
            or_(
                ParcelaPagamento.forma_pagamento == forma_pagamento,
                ParcelaPagamento.forma_pagamento_label.ilike(f"%{forma_pagamento}%"),
            )
        )
    parcelas = base.order_by(asc(ParcelaPagamento.data_vencimento)).all()
    if status:
        if status == "nao_pagos":
            parcelas = [p for p in parcelas if p.data_pagamento is None]
        else:
            parcelas = [p for p in parcelas if _status_parcela(p.data_vencimento, p.data_pagamento) == status]
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow([
        "Número NF", "Emitente", "Chave", "Vencimento", "Valor", "Forma pagamento", "Status", "Data pagamento"
    ])
    for p in parcelas:
        nota = db.query(NotaFiscal).filter_by(id=p.nota_id).first()
        st = _status_parcela(p.data_vencimento, p.data_pagamento)
        writer.writerow([
            nota.numero_nfe if nota else "",
            nota.emitente_nome if nota else "",
            nota.chave_acesso if nota else "",
            p.data_vencimento.strftime("%d/%m/%Y") if p.data_vencimento else "",
            str(p.valor) if p.valor is not None else "0",
            p.forma_pagamento_label or "Outros",
            st,
            p.data_pagamento.strftime("%d/%m/%Y") if p.data_pagamento else "",
        ])
    output.seek(0)
    return Response(
        content=output.getvalue().encode("utf-8-sig"),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=financeiro_parcelas_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        },
    )
