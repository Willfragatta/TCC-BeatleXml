"""API para o aplicativo Beatle Uploader (upload automático de NFe/NFCe emitidas)."""
import asyncio
import re
import logging
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List, Optional

from app.models.database import Empresa, NotaFiscal, Usuario
from app.core.dependencies import get_db, get_usuario_atual
from app.core.auth import criar_token, verificar_senha
from app.core.recaptcha import client_ip
from app.core.rate_limit import verificar_limite, registrar_tentativa, limpar_sucesso
from app.services.parser import XMLParser, decodificar_xml_bytes
from app.services.parcelas import gravar_parcelas

router = APIRouter()
logger = logging.getLogger("beatle_xml")


def _normalizar_cnpj_14(val: str) -> str:
    if not val:
        return ""
    n = "".join(c for c in str(val) if c.isdigit())
    return n.zfill(14)[:14]


class LoginRequest(BaseModel):
    email: str
    senha: str


class HeartbeatRequest(BaseModel):
    empresa_id: int
    pasta_monitorada: Optional[str] = None
    versao: Optional[str] = None


@router.post("/login")
async def uploader_login(
    body: LoginRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Login para o Beatle Uploader. Retorna token JWT e lista de empresas."""
    ip = client_ip(request)
    email = body.email.strip().lower()
    limite = verificar_limite(db, "uploader_login", ip, email)
    if limite:
        raise HTTPException(status_code=429, detail=limite)

    usuario = db.query(Usuario).filter_by(email=email).first()
    if not usuario or not verificar_senha(body.senha, usuario.senha_hash):
        registrar_tentativa(db, "uploader_login", ip, email)
        raise HTTPException(status_code=401, detail="Email ou senha incorretos")

    if (getattr(usuario, "status", None) or "ativo") != "ativo":
        raise HTTPException(status_code=403, detail="Conta não liberada. Fale com o suporte.")

    empresas = db.query(Empresa).filter_by(usuario_id=usuario.id).all()
    if not empresas:
        raise HTTPException(status_code=403, detail="Nenhuma empresa cadastrada para este usuário")

    limpar_sucesso(db, "uploader_login", email=email)
    token = criar_token(usuario.id)
    return {
        "token": token,
        "empresas": [
            {"id": e.id, "razao_social": e.razao_social or "", "cnpj": e.cnpj or ""}
            for e in empresas
        ],
    }


@router.post("/upload")
async def uploader_upload(
    empresa_id: int = Form(...),
    arquivos: List[UploadFile] = File(..., description="Arquivos XML"),
    usuario=Depends(get_usuario_atual),
    db: Session = Depends(get_db),
):
    """Recebe XMLs do uploader. Valida emitente = empresa e grava com origem=EMITIDA."""
    empresa = db.query(Empresa).filter_by(id=empresa_id, usuario_id=usuario.id).first()
    if not empresa:
        raise HTTPException(status_code=404, detail="Empresa não encontrada ou sem acesso")

    cnpj_empresa = _normalizar_cnpj_14(empresa.cnpj)
    importados = 0
    erros = 0

    for arquivo in arquivos:
        if not arquivo.filename or not arquivo.filename.lower().endswith(".xml"):
            continue
        try:
            raw = await arquivo.read()
            conteudo_xml = decodificar_xml_bytes(raw)
            if not conteudo_xml:
                erros += 1
                continue
        except Exception as e:
            logger.warning(f"Uploader: erro ao ler {arquivo.filename}: {e}")
            erros += 1
            continue

        try:
            parser = XMLParser(conteudo_xml)
            if parser.root is None:
                logger.warning(f"Uploader: XML inválido: {arquivo.filename}")
                erros += 1
                continue
            emit_cnpj = parser.extrair_emitente_cnpj()
            if not emit_cnpj or emit_cnpj != cnpj_empresa:
                logger.warning(f"Uploader: XML {arquivo.filename} - emitente {emit_cnpj} não bate com empresa {cnpj_empresa}")
                erros += 1
                continue

            modelo = parser.extrair_modelo()
            if modelo not in ("55", "65"):
                logger.warning(f"Uploader: XML {arquivo.filename} - modelo inválido (não é 55 ou 65)")
                erros += 1
                continue

            chave = re.search(r'chNFe="(\d+)"|chNFe>(\d+)<', conteudo_xml)
            chave_limpa = chave.group(1) or chave.group(2) if chave else arquivo.filename.replace(".xml", "")

            info = parser.extrair_todos_os_dados()
            # Para notas emitidas, exibimos o destinatário (cliente), não o emitente (nós)
            dest_nome = parser.extrair_destinatario_nome()
            display_nome = (dest_nome or info.get("emitente") or "").strip() or "Desconhecido"
            data_emissao = None
            if info.get("data"):
                s = str(info["data"]).strip()
                try:
                    if "T" in s:
                        data_emissao = datetime.fromisoformat(s.replace("Z", "+00:00")[:19])
                    else:
                        data_emissao = datetime.strptime(s[:10], "%Y-%m-%d")
                except Exception:
                    pass
            if not data_emissao:
                data_emissao = datetime.now()

            serie = info.get("serie")
            nota_existente = db.query(NotaFiscal).filter_by(chave_acesso=chave_limpa).first()
            if not nota_existente:
                nova = NotaFiscal(
                    empresa_id=empresa_id,
                    chave_acesso=chave_limpa,
                    numero_nfe=info.get("numero"),
                    serie=serie,
                    emitente_nome=display_nome,
                    emitente_cnpj=emit_cnpj or info.get("emitente_cnpj"),
                    natureza_operacao=info.get("natureza_operacao"),
                    valor_total=info.get("valor"),
                    data_emissao=data_emissao,
                    xml_conteudo=conteudo_xml,
                    status_manifesto=info.get("tipo_operacao") or "COMPLETA",
                    origem="EMITIDA",
                    modelo=modelo,
                )
                db.add(nova)
                db.flush()
                gravar_parcelas(db, nova)
            else:
                nota_existente.xml_conteudo = conteudo_xml
                nota_existente.numero_nfe = info.get("numero")
                nota_existente.serie = serie
                nota_existente.emitente_nome = display_nome
                nota_existente.emitente_cnpj = emit_cnpj or info.get("emitente_cnpj") or nota_existente.emitente_cnpj
                nota_existente.natureza_operacao = info.get("natureza_operacao") or nota_existente.natureza_operacao
                nota_existente.valor_total = info.get("valor")
                nota_existente.data_emissao = data_emissao
                nota_existente.status_manifesto = info.get("tipo_operacao") or "COMPLETA"
                nota_existente.origem = "EMITIDA"
                nota_existente.modelo = modelo
                db.flush()
                gravar_parcelas(db, nota_existente)

            importados += 1
            db.commit()
        except Exception as e:
            logger.warning(f"Uploader: erro ao importar {arquivo.filename}: {e}")
            db.rollback()
            erros += 1

    return {"status": "ok", "importados": importados, "erros": erros}


@router.post("/heartbeat")
async def uploader_heartbeat(
    body: HeartbeatRequest,
    usuario=Depends(get_usuario_atual),
    db: Session = Depends(get_db),
):
    """Registra heartbeat do uploader para monitor no suporte."""
    empresa = db.query(Empresa).filter_by(id=body.empresa_id, usuario_id=usuario.id).first()
    if not empresa:
        raise HTTPException(status_code=404, detail="Empresa não encontrada ou sem acesso")

    empresa.uploader_ultimo_heartbeat = datetime.now()
    if body.pasta_monitorada is not None:
        empresa.uploader_pasta_monitorada = body.pasta_monitorada[:500] if body.pasta_monitorada else None
    if body.versao is not None:
        empresa.uploader_versao = body.versao[:20] if body.versao else None
    db.commit()

    return {"status": "ok"}


