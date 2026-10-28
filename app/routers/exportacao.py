from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session
from typing import Optional
import csv
import io
from datetime import datetime
from app.models.database import NotaFiscal, NotaEtiqueta, Etiqueta
from app.core.dependencies import get_db, get_usuario_atual, get_empresa_ativa
from app.routers.notas import _aplicar_filtros_query, _calcular_dias_atraso

router = APIRouter()

@router.get("/notas/csv")
async def exportar_notas_csv(
    busca: Optional[str] = None,
    etiqueta_id: Optional[int] = None,
    sem_etiquetas: Optional[bool] = False,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    erp_baixada: Optional[str] = None,
    erp_recebida: Optional[str] = None,
    erp_lancada: Optional[str] = None,
    atraso_min_dias: Optional[int] = None,
    somente_nao_lancadas: Optional[bool] = False,
    usuario = Depends(get_usuario_atual),
    empresa = Depends(get_empresa_ativa),
    db: Session = Depends(get_db)
):
    """Exporta notas fiscais para CSV."""
    query = db.query(NotaFiscal).filter(NotaFiscal.empresa_id == empresa.id)
    query = _aplicar_filtros_query(
        query, db, empresa.id, usuario.id,
        busca=busca, etiqueta_id=etiqueta_id, sem_etiquetas=sem_etiquetas,
        data_inicio=data_inicio, data_fim=data_fim,
        erp_baixada=erp_baixada, erp_recebida=erp_recebida, erp_lancada=erp_lancada,
        atraso_min_dias=atraso_min_dias, somente_nao_lancadas=somente_nao_lancadas,
    )
    notas = query.order_by(NotaFiscal.id.desc()).limit(50_000).all()

    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')

    writer.writerow([
        'ID', 'Chave de Acesso', 'Número NFE', 'Emitente',
        'Data Emissão', 'Valor Total', 'Status', 'Dias Atraso', 'Etiquetas'
    ])

    for nota in notas:
        etiquetas = db.query(Etiqueta).join(NotaEtiqueta).filter(
            NotaEtiqueta.nota_id == nota.id,
            Etiqueta.usuario_id == usuario.id
        ).all()
        etiquetas_str = ', '.join([e.nome for e in etiquetas]) if etiquetas else ''
        dias = _calcular_dias_atraso(nota)

        writer.writerow([
            nota.id,
            nota.chave_acesso or '',
            nota.numero_nfe or '',
            nota.emitente_nome or '',
            nota.data_emissao.strftime('%d/%m/%Y %H:%M') if nota.data_emissao else '',
            str(nota.valor_total) if nota.valor_total else '0',
            nota.status_manifesto or '',
            '' if dias is None else str(dias),
            etiquetas_str
        ])

    output.seek(0)
    return Response(
        content=output.getvalue().encode('utf-8-sig'),
        media_type='text/csv',
        headers={
            'Content-Disposition': f'attachment; filename=notas_fiscais_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
        }
    )


@router.get("/notas/excel")
async def exportar_notas_excel(
    busca: Optional[str] = None,
    etiqueta_id: Optional[int] = None,
    sem_etiquetas: Optional[bool] = False,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    erp_baixada: Optional[str] = None,
    erp_recebida: Optional[str] = None,
    erp_lancada: Optional[str] = None,
    atraso_min_dias: Optional[int] = None,
    somente_nao_lancadas: Optional[bool] = False,
    usuario=Depends(get_usuario_atual),
    empresa=Depends(get_empresa_ativa),
    db: Session = Depends(get_db),
):
    """Exporta notas fiscais para Excel (CSV formatado para Excel)."""
    return await exportar_notas_csv(
        busca=busca,
        etiqueta_id=etiqueta_id,
        sem_etiquetas=sem_etiquetas,
        data_inicio=data_inicio,
        data_fim=data_fim,
        erp_baixada=erp_baixada,
        erp_recebida=erp_recebida,
        erp_lancada=erp_lancada,
        atraso_min_dias=atraso_min_dias,
        somente_nao_lancadas=somente_nao_lancadas,
        usuario=usuario,
        empresa=empresa,
        db=db,
    )
