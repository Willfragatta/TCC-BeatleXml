from datetime import datetime, timedelta, date
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Date, cast, exists, func, desc
from sqlalchemy.orm import Session

from app.models.database import (
    NotaFiscal, NotaEtiqueta,
)
from app.core.dependencies import get_db, get_usuario_atual, get_empresa_ativa

router = APIRouter()

MESES_PT = (
    "", "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
)


def periodo_mes_civil(
    mes: Optional[str], hoje: Optional[date] = None
) -> Tuple[date, date, str]:
    """(ini, fim, YYYY-MM) do mês civil. Default: mês atual."""
    hoje = hoje or date.today()
    ini = date(hoje.year, hoje.month, 1)
    if mes and len(mes) >= 7 and mes[4] == "-":
        try:
            ano, m = int(mes[:4]), int(mes[5:7])
            if 1 <= m <= 12:
                ini = date(ano, m, 1)
        except ValueError:
            pass
    if ini.month == 12:
        fim = date(ini.year + 1, 1, 1) - timedelta(days=1)
    else:
        fim = date(ini.year, ini.month + 1, 1) - timedelta(days=1)
    return ini, fim, f"{ini.year:04d}-{ini.month:02d}"


@router.get("/")
async def obter_dashboard(
    mes: Optional[str] = Query(None, description="YYYY-MM do painel"),
    usuario=Depends(get_usuario_atual),
    empresa=Depends(get_empresa_ativa),
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """Retorna estatísticas do dashboard."""

    agora = datetime.now()
    inicio_mes = agora.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    total_notas_mes = db.query(NotaFiscal).filter(
        NotaFiscal.empresa_id == empresa.id,
        NotaFiscal.data_emissao >= inicio_mes
    ).count()

    valor_total_mes = db.query(func.sum(NotaFiscal.valor_total)).filter(
        NotaFiscal.empresa_id == empresa.id,
        NotaFiscal.data_emissao >= inicio_mes,
        NotaFiscal.valor_total.isnot(None)
    ).scalar() or 0

    tem_etiqueta = exists().where(NotaEtiqueta.nota_id == NotaFiscal.id)
    notas_sem_etiqueta = db.query(func.count(NotaFiscal.id)).filter(
        NotaFiscal.empresa_id == empresa.id,
        ~tem_etiqueta,
    ).scalar() or 0

    ultima_sync = empresa.ultima_consulta
    if ultima_sync:
        tempo_desde_sync = agora - ultima_sync
        minutos_desde_sync = int(tempo_desde_sync.total_seconds() / 60)
    else:
        minutos_desde_sync = None

    top_fornecedores_valor = db.query(
        NotaFiscal.emitente_nome,
        func.count(NotaFiscal.id).label('total_notas'),
        func.sum(NotaFiscal.valor_total).label('valor_total')
    ).filter(
        NotaFiscal.empresa_id == empresa.id,
        NotaFiscal.data_emissao >= inicio_mes,
        NotaFiscal.emitente_nome.isnot(None)
    ).group_by(NotaFiscal.emitente_nome).order_by(desc('valor_total')).limit(10).all()

    top_fornecedores_qtd = db.query(
        NotaFiscal.emitente_nome,
        func.count(NotaFiscal.id).label('total_notas'),
        func.sum(NotaFiscal.valor_total).label('valor_total')
    ).filter(
        NotaFiscal.empresa_id == empresa.id,
        NotaFiscal.data_emissao >= inicio_mes,
        NotaFiscal.emitente_nome.isnot(None)
    ).group_by(NotaFiscal.emitente_nome).order_by(desc('total_notas')).limit(10).all()

    inicio_30 = (agora - timedelta(days=29)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    dia_col = cast(NotaFiscal.data_emissao, Date)
    por_dia_rows = db.query(
        dia_col,
        func.count(NotaFiscal.id),
        func.coalesce(func.sum(NotaFiscal.valor_total), 0),
    ).filter(
        NotaFiscal.empresa_id == empresa.id,
        NotaFiscal.data_emissao >= inicio_30,
    ).group_by(dia_col).all()
    mapa_dia = {row[0]: (int(row[1] or 0), float(row[2] or 0)) for row in por_dia_rows}

    notas_por_dia = []
    for i in range(29, -1, -1):
        dia = (agora - timedelta(days=i)).date()
        count, valor = mapa_dia.get(dia, (0, 0.0))
        notas_por_dia.append({
            "data": dia.strftime("%Y-%m-%d"),
            "data_formatada": dia.strftime("%d/%m"),
            "total_notas": count,
            "valor_total": float(valor),
        })

    alertas = []

    notas_antigas = db.query(func.count(NotaFiscal.id)).filter(
        NotaFiscal.empresa_id == empresa.id,
        NotaFiscal.data_emissao < agora - timedelta(days=30),
        ~tem_etiqueta,
    ).scalar() or 0

    if notas_antigas > 0:
        alertas.append({
            "tipo": "warning",
            "mensagem": f"{notas_antigas} nota(s) sem etiqueta há mais de 30 dias"
        })

    if empresa.bloqueio_656_ate and agora < empresa.bloqueio_656_ate:
        tempo_restante = empresa.bloqueio_656_ate - agora
        horas = int(tempo_restante.total_seconds() / 3600)
        minutos = int((tempo_restante.total_seconds() % 3600) / 60)

        if horas > 0:
            mensagem = (
                "Sistema aguardando liberação da Receita Federal. Isso é normal e acontece "
                f"automaticamente. Aguarde aproximadamente {horas} hora(s) e {minutos} minuto(s). "
                "Você não precisa fazer nada - o sistema continuará automaticamente quando liberado."
            )
        else:
            mensagem = (
                "Sistema aguardando liberação da Receita Federal. Isso é normal e acontece "
                f"automaticamente. Aguarde aproximadamente {minutos} minuto(s). Você não precisa "
                "fazer nada - o sistema continuará automaticamente quando liberado."
            )

        alertas.append({
            "tipo": "warning",
            "mensagem": mensagem
        })

    return {
        "total_notas_mes": total_notas_mes,
        "valor_total_mes": float(valor_total_mes),
        "notas_sem_etiqueta": notas_sem_etiqueta,
        "ultima_sync": ultima_sync.isoformat() if ultima_sync else None,
        "ultima_sync_mensagem": empresa.ultima_sync_mensagem,
        "minutos_desde_sync": minutos_desde_sync,
        "top_fornecedores_valor": [
            {
                "nome": f[0],
                "total_notas": f[1],
                "valor_total": float(f[2] or 0),
            }
            for f in top_fornecedores_valor
        ],
        "top_fornecedores_qtd": [
            {
                "nome": f[0],
                "total_notas": f[1],
                "valor_total": float(f[2] or 0),
            }
            for f in top_fornecedores_qtd
        ],
        "notas_por_dia": notas_por_dia,
        "alertas": alertas,
    }
