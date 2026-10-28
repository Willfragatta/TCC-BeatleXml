from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_, desc, asc, func, cast, Date
from typing import Optional, List, Any, Dict
from pydantic import BaseModel
import os
import zipfile
import io
import tempfile
from datetime import datetime, timedelta, date
from app.models.database import NotaFiscal, NotaEtiqueta, Etiqueta
from app.core.dependencies import get_db, get_usuario_atual, get_empresa_ativa
from app.services.parser import XMLParser
from app.services.reprocessar_resumos import buscar_completa_para_nota_id
from brazilfiscalreport.danfe import Danfe

router = APIRouter()

LIMITE_DOWNLOAD_FILTROS = 50_000
LIMITE_DOWNLOAD_MES = 30_000


def _expr_dias_atraso():
    """Expressão SQL: dias entre data_emissao e hoje (PostgreSQL)."""
    return func.current_date() - cast(NotaFiscal.data_emissao, Date)


def _filtro_bool_erp(coluna, valor: Optional[str]):
    """Tri-state: sim | nao | None/vazio. Retorna cláusula ou None."""
    if not valor:
        return None
    v = str(valor).strip().lower()
    if v in ("sim", "true", "1", "s"):
        return coluna.is_(True)
    if v in ("nao", "não", "false", "0", "n"):
        return or_(coluna.is_(False), coluna.is_(None))
    return None


def _calcular_dias_atraso(nota: NotaFiscal) -> Optional[int]:
    """Dias desde a emissão. None quando a nota não tem data."""
    if not nota.data_emissao:
        return None
    d = nota.data_emissao.date() if hasattr(nota.data_emissao, "date") else nota.data_emissao
    try:
        return max(0, (date.today() - d).days)
    except Exception:
        return None


def _aplicar_filtros_query(
    query,
    db: Session,
    empresa_id: int,
    usuario_id: int,
    *,
    busca: Optional[str] = None,
    tipo_busca: Optional[str] = None,
    etiqueta_id: Optional[int] = None,
    sem_etiquetas: Optional[bool] = False,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    situacao: Optional[str] = None,
    origem: Optional[str] = None,
    modelo: Optional[str] = None,
    valor_min: Optional[float] = None,
    valor_max: Optional[float] = None,
    serie: Optional[str] = None,
    erp_baixada: Optional[str] = None,
    erp_recebida: Optional[str] = None,
    erp_lancada: Optional[str] = None,
    atraso_min_dias: Optional[int] = None,
    somente_nao_lancadas: Optional[bool] = False,
):
    """Aplica os mesmos filtros da listagem. Retorna a query modificada."""
    if origem == "EMITIDA":
        query = query.filter(NotaFiscal.origem == "EMITIDA")
        if modelo in ("55", "65"):
            query = query.filter(NotaFiscal.modelo == modelo)
    elif origem != "EMITIDA":
        query = query.filter(
            or_(NotaFiscal.origem == "RECEBIDA", NotaFiscal.origem.is_(None))
        )

    if busca and busca.strip():
        busca_ = busca.strip()
        if tipo_busca == "numero_nfe":
            query = query.filter(NotaFiscal.numero_nfe == busca_)
        elif tipo_busca == "chave":
            query = query.filter(NotaFiscal.chave_acesso.ilike(f"%{busca_}%"))
        elif tipo_busca == "empresa":
            query = query.filter(
                or_(
                    NotaFiscal.emitente_nome.ilike(f"%{busca_}%"),
                    and_(NotaFiscal.xml_conteudo.isnot(None), NotaFiscal.xml_conteudo.ilike(f"%{busca_}%"))
                )
            )
        elif tipo_busca == "conteudo_nfe":
            query = query.filter(
                NotaFiscal.xml_conteudo.isnot(None),
                NotaFiscal.xml_conteudo.ilike(f"%{busca_}%")
            )
        else:
            query = query.filter(
                or_(
                    NotaFiscal.emitente_nome.ilike(f"%{busca_}%"),
                    NotaFiscal.chave_acesso.ilike(f"%{busca_}%"),
                    NotaFiscal.numero_nfe.ilike(f"%{busca_}%")
                )
            )

    if etiqueta_id:
        nota_ids = [row[0] for row in db.query(NotaEtiqueta.nota_id).filter(
            NotaEtiqueta.etiqueta_id == etiqueta_id
        ).all()]
        if nota_ids:
            query = query.filter(NotaFiscal.id.in_(nota_ids))
        else:
            query = query.filter(NotaFiscal.id == -1)

    if sem_etiquetas:
        nota_ids_com_etiqueta = [row[0] for row in db.query(NotaEtiqueta.nota_id)
            .join(NotaFiscal, NotaFiscal.id == NotaEtiqueta.nota_id)
            .filter(NotaFiscal.empresa_id == empresa_id)
            .distinct().all()]
        if nota_ids_com_etiqueta:
            query = query.filter(~NotaFiscal.id.in_(nota_ids_com_etiqueta))

    if data_inicio:
        try:
            data_ini = datetime.strptime(data_inicio, "%Y-%m-%d")
            query = query.filter(NotaFiscal.data_emissao >= data_ini)
        except Exception:
            pass
    if data_fim:
        try:
            data_f = datetime.strptime(data_fim, "%Y-%m-%d") + timedelta(days=1)
            query = query.filter(NotaFiscal.data_emissao < data_f)
        except Exception:
            pass

    if situacao == "canceladas":
        query = query.filter(NotaFiscal.status_manifesto == "CANCELADA")
    else:
        query = query.filter(NotaFiscal.status_manifesto != "CANCELADA")

    if valor_min is not None:
        query = query.filter(NotaFiscal.valor_total >= valor_min)
    if valor_max is not None:
        query = query.filter(NotaFiscal.valor_total <= valor_max)
    if serie and serie.strip():
        query = query.filter(NotaFiscal.serie == serie.strip())

    if atraso_min_dias is not None:
        try:
            min_d = max(0, int(atraso_min_dias))
        except (TypeError, ValueError):
            min_d = 0
        query = query.filter(_expr_dias_atraso() >= min_d)

    return query


def _gerar_danfce_pdf(xml_conteudo: str) -> bytes:
    """Gera PDF do DANFC-e (NFCe) usando erpbrasil.edoc.pdf (ImprimirXml)."""
    try:
        from erpbrasil.edoc.pdf.base import ImprimirXml
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="Biblioteca erpbrasil.edoc.pdf não instalada. Instale com: pip install erpbrasil.edoc.pdf"
        )
    xml_bytes = xml_conteudo.encode("utf-8") if isinstance(xml_conteudo, str) else xml_conteudo
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".xml", delete=False) as tmp:
        tmp.write(xml_bytes)
        tmp_path = tmp.name
    try:
        # ImprimirXml.imprimir(caminho_xml=...) retorna bytes do PDF quando output_dir não é passado
        result = ImprimirXml.imprimir(caminho_xml=tmp_path)
        if isinstance(result, bytes):
            return result
        # Se retornar caminho do arquivo (quando output_dir foi usado em outro fluxo)
        with open(result, "rb") as f:
            return f.read()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao gerar DANFC-e: {str(e)}")
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def gerar_danfe_pdf(xml_conteudo: str, modelo: Optional[str] = None) -> bytes:
    """Gera PDF da DANFE (NFe) ou DANFC-e (NFCe) a partir do XML. modelo 65 = NFCe."""
    if modelo == "65":
        try:
            return _gerar_danfce_pdf(xml_conteudo)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Erro ao gerar DANFC-e: {str(e)}")
    danfe = Danfe(xml=xml_conteudo)
    buffer = io.BytesIO()
    try:
        danfe.output(buffer)
        return buffer.getvalue()
    except Exception:
        with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
            danfe.output(tmp.name)
            tmp.seek(0)
            return tmp.read()

@router.get("/")
async def listar_notas(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    busca: Optional[str] = None,
    tipo_busca: Optional[str] = Query(None, description="Tipo: numero_nfe, chave, empresa, conteudo_nfe"),
    etiqueta_id: Optional[int] = None,
    sem_etiquetas: Optional[bool] = False,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    situacao: Optional[str] = None,
    origem: Optional[str] = Query(None, description="RECEBIDA ou EMITIDA"),
    modelo: Optional[str] = Query(None, description="55 (NFe) ou 65 (NFCe)"),
    order_by: Optional[str] = Query(
        "data_emissao",
        description="Campo: data_emissao, numero_nfe, emitente_nome, valor_total, serie, dias_atraso",
    ),
    order_dir: Optional[str] = Query("desc", description="Direção: asc ou desc"),
    valor_min: Optional[float] = Query(None, description="Valor mínimo (NFCe)"),
    valor_max: Optional[float] = Query(None, description="Valor máximo (NFCe)"),
    serie: Optional[str] = Query(None, description="Série do documento (NFCe)"),
    erp_baixada: Optional[str] = Query(None, description="sim | nao"),
    erp_recebida: Optional[str] = Query(None, description="sim | nao"),
    erp_lancada: Optional[str] = Query(None, description="sim | nao"),
    atraso_min_dias: Optional[int] = Query(None, ge=0, description="Mínimo de dias de atraso (não lançadas)"),
    somente_nao_lancadas: Optional[bool] = Query(False, description="Somente notas ainda não lançadas"),
    usuario = Depends(get_usuario_atual),
    empresa = Depends(get_empresa_ativa),
    db: Session = Depends(get_db)
):
    """Lista notas com filtros, paginação e ordenação."""
    query = db.query(NotaFiscal).filter(NotaFiscal.empresa_id == empresa.id)
    query = _aplicar_filtros_query(
        query, db, empresa.id, usuario.id,
        busca=busca, tipo_busca=tipo_busca,
        etiqueta_id=etiqueta_id, sem_etiquetas=sem_etiquetas,
        data_inicio=data_inicio, data_fim=data_fim,
        situacao=situacao, origem=origem, modelo=modelo,
        valor_min=valor_min, valor_max=valor_max, serie=serie,
        erp_baixada=erp_baixada, erp_recebida=erp_recebida, erp_lancada=erp_lancada,
        atraso_min_dias=atraso_min_dias, somente_nao_lancadas=somente_nao_lancadas,
    )

    # Ordenação por atraso: só notas com número (não lançadas)
    if order_by == "dias_atraso":
        query = query.filter(NotaFiscal.data_emissao.isnot(None))
        dias_expr = _expr_dias_atraso()
        if order_dir == "asc":
            query = query.order_by(asc(dias_expr), desc(NotaFiscal.id))
        else:
            query = query.order_by(desc(dias_expr), desc(NotaFiscal.id))
    else:
        campo_ordenacao_map = {
            "data_emissao": NotaFiscal.data_emissao,
            "numero_nfe": NotaFiscal.numero_nfe,
            "emitente_nome": NotaFiscal.emitente_nome,
            "valor_total": NotaFiscal.valor_total,
        }
        campo_ordenacao = campo_ordenacao_map.get(order_by, NotaFiscal.data_emissao)
        if order_dir == "asc":
            query = query.order_by(asc(campo_ordenacao))
        else:
            query = query.order_by(desc(campo_ordenacao))

    total = query.count()
    notas = query.offset(skip).limit(limit).all()

    notas_com_etiquetas = []
    for nota in notas:
        if nota.xml_conteudo and (not nota.natureza_operacao or not nota.emitente_cnpj):
            try:
                p = XMLParser(nota.xml_conteudo)
                info = p.extrair_todos_os_dados()
                alterou = False
                if not nota.natureza_operacao and info.get("natureza_operacao"):
                    nota.natureza_operacao = info.get("natureza_operacao")
                    alterou = True
                if not nota.emitente_cnpj and info.get("emitente_cnpj"):
                    nota.emitente_cnpj = info.get("emitente_cnpj")
                    alterou = True
                if alterou:
                    db.commit()
            except Exception:
                pass

        nota_dict = {
            "id": nota.id,
            "chave_acesso": nota.chave_acesso,
            "numero_nfe": nota.numero_nfe,
            "serie": nota.serie,
            "emitente_nome": nota.emitente_nome,
            "emitente_cnpj": nota.emitente_cnpj,
            "natureza_operacao": nota.natureza_operacao or "",
            "valor_total": float(nota.valor_total) if nota.valor_total else 0,
            "data_emissao": nota.data_emissao.isoformat() if nota.data_emissao else None,
            "status_manifesto": nota.status_manifesto,
            "erp_baixada": False,
            "erp_recebida": False,
            "erp_lancada": False,
            "erp_transacao": "",
            "dias_atraso": _calcular_dias_atraso(nota),
            "etiquetas": []
        }

        etiquetas = db.query(Etiqueta).join(NotaEtiqueta).filter(
            NotaEtiqueta.nota_id == nota.id,
            Etiqueta.usuario_id == usuario.id
        ).all()

        nota_dict["etiquetas"] = [
            {
                "id": e.id,
                "nome": e.nome,
                "cor": e.cor,
                "cor_fundo": e.cor_fundo or None,
                "cor_texto": e.cor_texto or None,
            }
            for e in etiquetas
        ]

        notas_com_etiquetas.append(nota_dict)

    return {
        "notas": notas_com_etiquetas,
        "total": total,
        "skip": skip,
        "limit": limit,
        "exibir_status_erp": False,
    }

@router.post("/{nota_id}/buscar-completa")
async def buscar_completa(
    nota_id: int,
    usuario=Depends(get_usuario_atual),
    empresa=Depends(get_empresa_ativa),
    db: Session = Depends(get_db),
):
    """Tenta buscar o XML completo para uma nota que está como RESUMO."""
    ok, erro = buscar_completa_para_nota_id(db, nota_id, empresa.id)
    if ok:
        return {"ok": True}
    return {"ok": False, "erro": erro or "Erro ao buscar nota completa"}


class FiltrosDownloadBody(BaseModel):
    """Filtros para download em lote (mesmos da listagem)."""
    origem: Optional[str] = None
    modelo: Optional[str] = None
    busca: Optional[str] = None
    tipo_busca: Optional[str] = None
    data_inicio: Optional[str] = None
    data_fim: Optional[str] = None
    situacao: Optional[str] = None
    etiqueta_id: Optional[int] = None
    sem_etiquetas: Optional[bool] = None
    valor_min: Optional[float] = None
    valor_max: Optional[float] = None
    serie: Optional[str] = None
    erp_baixada: Optional[str] = None
    erp_recebida: Optional[str] = None
    erp_lancada: Optional[str] = None
    atraso_min_dias: Optional[int] = None
    somente_nao_lancadas: Optional[bool] = None


class DownloadLoteBody(BaseModel):
    """Corpo: ids (lista) ou filtros (objeto)."""
    ids: Optional[List[int]] = None
    filtros: Optional[FiltrosDownloadBody] = None


@router.post("/download-lote")
async def download_lote(
    body: DownloadLoteBody,
    usuario = Depends(get_usuario_atual),
    empresa = Depends(get_empresa_ativa),
    db: Session = Depends(get_db)
):
    """Download de múltiplas notas em ZIP. Envie ids=[...] ou filtros={...} para todos os resultados da busca."""
    notas: List[NotaFiscal] = []
    if body.ids:
        if not body.ids:
            raise HTTPException(status_code=400, detail="Nenhuma nota selecionada")
        notas = db.query(NotaFiscal).filter(
            NotaFiscal.id.in_(body.ids),
            NotaFiscal.empresa_id == empresa.id
        ).all()
    elif body.filtros:
        query = db.query(NotaFiscal).filter(NotaFiscal.empresa_id == empresa.id)
        f = body.filtros
        query = _aplicar_filtros_query(
            query, db, empresa.id, usuario.id,
            busca=f.busca, tipo_busca=f.tipo_busca,
            data_inicio=f.data_inicio, data_fim=f.data_fim,
            situacao=f.situacao, origem=f.origem, modelo=f.modelo,
            etiqueta_id=f.etiqueta_id, sem_etiquetas=f.sem_etiquetas,
            valor_min=f.valor_min, valor_max=f.valor_max, serie=f.serie,
            erp_baixada=f.erp_baixada, erp_recebida=f.erp_recebida, erp_lancada=f.erp_lancada,
            atraso_min_dias=f.atraso_min_dias, somente_nao_lancadas=f.somente_nao_lancadas,
        )
        notas = query.limit(LIMITE_DOWNLOAD_FILTROS).all()
    else:
        raise HTTPException(status_code=400, detail="Envie 'ids' ou 'filtros' no corpo da requisição")

    if not notas:
        raise HTTPException(status_code=404, detail="Nenhuma nota encontrada")

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for nota in notas:
            if nota.xml_conteudo:
                zip_file.writestr(f"{nota.chave_acesso}.xml", nota.xml_conteudo)

    zip_buffer.seek(0)
    return Response(
        content=zip_buffer.read(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename=notas_fiscais_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        }
    )

@router.get("/danfe/{nota_id}")
async def danfe_individual(
    nota_id: int,
    download: bool = False,
    usuario = Depends(get_usuario_atual),
    empresa = Depends(get_empresa_ativa),
    db: Session = Depends(get_db)
):
    """Gera DANFE (PDF) de uma nota específica."""
    nota = db.query(NotaFiscal).filter(
        NotaFiscal.id == nota_id,
        NotaFiscal.empresa_id == empresa.id
    ).first()

    if not nota or not nota.xml_conteudo:
        raise HTTPException(status_code=404, detail="XML da nota não encontrado")

    # DANFE exige XML completo (nfeProc/procNFe)
    if "<nfeProc" not in nota.xml_conteudo and "<procNFe" not in nota.xml_conteudo:
        raise HTTPException(status_code=400, detail="XML completo não disponível para gerar DANFE")

    pdf_bytes = gerar_danfe_pdf(nota.xml_conteudo, getattr(nota, "modelo", None) or "55")
    disposition = "attachment" if download else "inline"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"{disposition}; filename=danfe_{nota.chave_acesso}.pdf"
        }
    )

@router.post("/danfe-lote")
async def danfe_lote(
    body: DownloadLoteBody,
    usuario = Depends(get_usuario_atual),
    empresa = Depends(get_empresa_ativa),
    db: Session = Depends(get_db)
):
    """Download de múltiplas DANFEs em ZIP. Envie ids=[...] ou filtros={...}."""
    notas: List[NotaFiscal] = []
    if body.ids:
        if not body.ids:
            raise HTTPException(status_code=400, detail="Nenhuma nota selecionada")
        notas = db.query(NotaFiscal).filter(
            NotaFiscal.id.in_(body.ids),
            NotaFiscal.empresa_id == empresa.id
        ).all()
    elif body.filtros:
        query = db.query(NotaFiscal).filter(NotaFiscal.empresa_id == empresa.id)
        f = body.filtros
        query = _aplicar_filtros_query(
            query, db, empresa.id, usuario.id,
            busca=f.busca, tipo_busca=f.tipo_busca,
            data_inicio=f.data_inicio, data_fim=f.data_fim,
            situacao=f.situacao, origem=f.origem, modelo=f.modelo,
            etiqueta_id=f.etiqueta_id, sem_etiquetas=f.sem_etiquetas,
            valor_min=f.valor_min, valor_max=f.valor_max, serie=f.serie,
            erp_baixada=f.erp_baixada, erp_recebida=f.erp_recebida, erp_lancada=f.erp_lancada,
            atraso_min_dias=f.atraso_min_dias, somente_nao_lancadas=f.somente_nao_lancadas,
        )
        notas = query.limit(LIMITE_DOWNLOAD_FILTROS).all()
    else:
        raise HTTPException(status_code=400, detail="Envie 'ids' ou 'filtros' no corpo")

    if not notas:
        raise HTTPException(status_code=404, detail="Nenhuma nota encontrada")

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for nota in notas:
            if not nota.xml_conteudo:
                continue
            if "<nfeProc" not in nota.xml_conteudo and "<procNFe" not in nota.xml_conteudo:
                continue
            pdf_bytes = gerar_danfe_pdf(nota.xml_conteudo, getattr(nota, "modelo", None) or "55")
            zip_file.writestr(f"{nota.chave_acesso}.pdf", pdf_bytes)

    zip_buffer.seek(0)
    return Response(
        content=zip_buffer.read(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename=danfes_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        }
    )


def _modelo_nota(nota) -> str:
    """Retorna '55' (NFe) ou '65' (NFCe). Notas sem modelo são tratadas como NFe."""
    return (getattr(nota, "modelo", None) or "55").strip()


@router.get("/download-mes")
async def download_mes(
    ano: int = Query(..., ge=2020, le=2100),
    mes: int = Query(..., ge=1, le=12),
    tipo: str = Query(
        "todos",
        description="xml_nfe | xml_nfce | danfe_nfe | danfe_nfce | todos (ZIP com 4 pastas)"
    ),
    usuario = Depends(get_usuario_atual),
    empresa = Depends(get_empresa_ativa),
    db: Session = Depends(get_db)
):
    """Download em ZIP das notas do mês/ano (emitidas). tipo=todos retorna ZIP com xmls_nfe/, xmls_nfce/, danfes_nfe/, danfes_nfce/."""
    tipos_validos = ("xml_nfe", "xml_nfce", "danfe_nfe", "danfe_nfce", "todos")
    if tipo not in tipos_validos:
        raise HTTPException(status_code=400, detail=f"tipo deve ser um de: {', '.join(tipos_validos)}")
    try:
        data_ini = datetime(ano, mes, 1)
        if mes == 12:
            data_fim = datetime(ano + 1, 1, 1)
        else:
            data_fim = datetime(ano, mes + 1, 1)
    except Exception:
        raise HTTPException(status_code=400, detail="Ano/mês inválido")

    query = db.query(NotaFiscal).filter(
        NotaFiscal.empresa_id == empresa.id,
        NotaFiscal.origem == "EMITIDA",
        NotaFiscal.data_emissao >= data_ini,
        NotaFiscal.data_emissao < data_fim,
    )
    # Filtrar por modelo quando não for "todos"
    if tipo == "xml_nfe" or tipo == "danfe_nfe":
        query = query.filter(or_(NotaFiscal.modelo == None, NotaFiscal.modelo == "55"))
    elif tipo == "xml_nfce" or tipo == "danfe_nfce":
        query = query.filter(NotaFiscal.modelo == "65")

    notas = query.order_by(NotaFiscal.data_emissao).limit(LIMITE_DOWNLOAD_MES).all()

    if not notas:
        raise HTTPException(status_code=404, detail="Nenhuma nota encontrada no período")

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for nota in notas:
            modelo = _modelo_nota(nota)
            tem_xml = bool(nota.xml_conteudo and nota.xml_conteudo.strip())

            if tipo == "todos":
                # ZIP com 4 pastas: xmls_nfe, xmls_nfce, danfes_nfe, danfes_nfce
                if nota.xml_conteudo:
                    pasta_xml = "xmls_nfe" if modelo == "55" else "xmls_nfce"
                    zip_file.writestr(f"{pasta_xml}/{nota.chave_acesso}.xml", nota.xml_conteudo)
                if nota.xml_conteudo and tem_xml:
                    try:
                        pdf_bytes = gerar_danfe_pdf(nota.xml_conteudo, modelo)
                        pasta_danfe = "danfes_nfe" if modelo == "55" else "danfes_nfce"
                        zip_file.writestr(f"{pasta_danfe}/{nota.chave_acesso}.pdf", pdf_bytes)
                    except Exception:
                        pass
            elif tipo == "xml_nfe" or tipo == "xml_nfce":
                if nota.xml_conteudo:
                    zip_file.writestr(f"{nota.chave_acesso}.xml", nota.xml_conteudo)
            elif (tipo == "danfe_nfe" or tipo == "danfe_nfce") and nota.xml_conteudo and tem_xml:
                try:
                    pdf_bytes = gerar_danfe_pdf(nota.xml_conteudo, modelo)
                    zip_file.writestr(f"{nota.chave_acesso}.pdf", pdf_bytes)
                except Exception:
                    pass

    zip_buffer.seek(0)
    nome = f"notas_{ano:04d}_{mes:02d}_{tipo}.zip"
    return Response(
        content=zip_buffer.read(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={nome}"}
    )

