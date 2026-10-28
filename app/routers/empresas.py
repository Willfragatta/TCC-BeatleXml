from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
import base64
import logging
import os
import re
from datetime import datetime
from app.models.database import Empresa, NotaFiscal
from app.core.dependencies import get_db, get_usuario_atual
from app.core.security import criptografar, descriptografar
from worker import validar_cnpj_base_certificado, extrair_cnpj_base
from app.services.parser import XMLParser, decodificar_xml_bytes

router = APIRouter()
logger = logging.getLogger("beatle_xml")

# Schemas Pydantic
class EmpresaCreate(BaseModel):
    cnpj: str
    razao_social: str
    ambiente: str = "1"
    uf_autor: str = "41"
    pasta_destino: Optional[str] = None

class EmpresaUpdate(BaseModel):
    razao_social: Optional[str] = None
    ambiente: Optional[str] = None
    uf_autor: Optional[str] = None
    pasta_destino: Optional[str] = None

class EmpresaResponse(BaseModel):
    id: int
    cnpj: str
    razao_social: str
    ambiente: str
    uf_autor: str
    ultimo_nsu: str
    ultima_consulta: Optional[datetime]
    bloqueio_656_ate: Optional[datetime]
    tem_certificado: bool
    certificado_valido_ate: Optional[datetime] = None
    dias_para_vencer: Optional[int] = None
    
    class Config:
        from_attributes = True

def verificar_validade_certificado(cert_pfx_bytes, cert_senha):
    """Verifica a validade do certificado e retorna data de expiração."""
    try:
        from cryptography.hazmat.primitives.serialization import pkcs12
        from cryptography.hazmat.backends import default_backend
        
        private_key, certificate, additional_certificates = pkcs12.load_key_and_certificates(
            cert_pfx_bytes, cert_senha.encode(), default_backend()
        )
        
        if certificate is None:
            return None, None
        
        # Data de validade do certificado (timezone-aware)
        validade_naive = certificate.not_valid_after
        # Converte para datetime sem timezone
        if validade_naive.tzinfo is None:
            validade = validade_naive
        else:
            validade = validade_naive.replace(tzinfo=None)
        
        agora = datetime.now()
        dias_restantes = (validade - agora).days
        
        return validade, dias_restantes
    except Exception as e:
        return None, None

@router.get("/", response_model=List[EmpresaResponse])
async def listar_empresas(
    usuario = Depends(get_usuario_atual),
    db: Session = Depends(get_db)
):
    """Lista todas as empresas do usuário (dono ou vínculo UsuarioEmpresa)."""
    from app.core.dependencies import ids_empresas_acessiveis
    ids = ids_empresas_acessiveis(db, usuario)
    if getattr(usuario, "is_admin", False) and not ids:
        empresas = db.query(Empresa).all()
    elif ids:
        empresas = db.query(Empresa).filter(Empresa.id.in_(ids)).all()
    else:
        empresas = []
    
    resultado = []
    for empresa in empresas:
        tem_certificado = bool(empresa.certificado_base64)
        validade = None
        dias_para_vencer = None
        
        if tem_certificado:
            try:
                cert_pfx_bytes = base64.b64decode(descriptografar(empresa.certificado_base64))
                cert_senha = descriptografar(empresa.certificado_senha)
                validade, dias_para_vencer = verificar_validade_certificado(cert_pfx_bytes, cert_senha)
            except:
                pass
        
        resultado.append({
            "id": empresa.id,
            "cnpj": empresa.cnpj,
            "razao_social": empresa.razao_social,
            "status": empresa.status,
            "ambiente": empresa.ambiente,
            "uf_autor": empresa.uf_autor,
            "ultimo_nsu": empresa.ultimo_nsu,
            "ultima_consulta": empresa.ultima_consulta,
            "bloqueio_656_ate": empresa.bloqueio_656_ate,
            "tem_certificado": tem_certificado,
            "certificado_valido_ate": validade,
            "dias_para_vencer": dias_para_vencer,
        })
    
    return resultado

@router.get("/{empresa_id}", response_model=EmpresaResponse)
async def obter_empresa(
    empresa_id: int,
    usuario = Depends(get_usuario_atual),
    db: Session = Depends(get_db)
):
    """Obtém detalhes de uma empresa."""
    from app.core.dependencies import usuario_acessa_empresa
    if not usuario_acessa_empresa(db, usuario, empresa_id):
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    empresa = db.query(Empresa).filter_by(id=empresa_id).first()
    if not empresa:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    
    tem_certificado = bool(empresa.certificado_base64)
    validade = None
    dias_para_vencer = None
    
    if tem_certificado:
        try:
            cert_pfx_bytes = base64.b64decode(descriptografar(empresa.certificado_base64))
            cert_senha = descriptografar(empresa.certificado_senha)
            validade, dias_para_vencer = verificar_validade_certificado(cert_pfx_bytes, cert_senha)
        except:
            pass
    
    return {
        "id": empresa.id,
        "cnpj": empresa.cnpj,
        "razao_social": empresa.razao_social,
        "ambiente": empresa.ambiente,
        "uf_autor": empresa.uf_autor,
        "ultimo_nsu": empresa.ultimo_nsu,
        "ultima_consulta": empresa.ultima_consulta,
        "bloqueio_656_ate": empresa.bloqueio_656_ate,
        "tem_certificado": tem_certificado,
        "certificado_valido_ate": validade,
        "dias_para_vencer": dias_para_vencer
    }

@router.post("/", response_model=EmpresaResponse, status_code=201)
async def criar_empresa(
    empresa_data: EmpresaCreate,
    usuario = Depends(get_usuario_atual),
    db: Session = Depends(get_db)
):
    """Cria uma nova empresa."""
    # Valida CNPJ
    cnpj_limpo = "".join(filter(str.isdigit, empresa_data.cnpj))
    if len(cnpj_limpo) != 14:
        raise HTTPException(status_code=400, detail="CNPJ deve ter 14 dígitos")
    
    # Verifica se já existe
    if db.query(Empresa).filter_by(cnpj=cnpj_limpo).first():
        raise HTTPException(status_code=400, detail="CNPJ já cadastrado")
    
    nova_empresa = Empresa(
        usuario_id=usuario.id,
        cnpj=cnpj_limpo,
        razao_social=empresa_data.razao_social,
        ambiente=empresa_data.ambiente,
        uf_autor=empresa_data.uf_autor,
        pasta_destino=empresa_data.pasta_destino,
        ultimo_nsu='000000000000000',  # Padrão SEFAZ: 15 dígitos zerados
        max_nsu_conhecido='000000000000000',  # Inicializa com zeros
        status='pendente_aprovacao'  # Nova empresa sempre começa pendente
    )
    
    db.add(nova_empresa)
    db.commit()
    db.refresh(nova_empresa)
    
    return {
        "id": nova_empresa.id,
        "cnpj": nova_empresa.cnpj,
        "razao_social": nova_empresa.razao_social,
        "ambiente": nova_empresa.ambiente,
        "uf_autor": nova_empresa.uf_autor,
        "ultimo_nsu": nova_empresa.ultimo_nsu,
        "ultima_consulta": nova_empresa.ultima_consulta,
        "bloqueio_656_ate": nova_empresa.bloqueio_656_ate,
        "tem_certificado": False,
        "certificado_valido_ate": None,
        "dias_para_vencer": None
    }

@router.put("/{empresa_id}", response_model=EmpresaResponse)
async def editar_empresa(
    empresa_id: int,
    empresa_data: EmpresaUpdate,
    usuario = Depends(get_usuario_atual),
    db: Session = Depends(get_db)
):
    """Edita uma empresa existente."""
    empresa = db.query(Empresa).filter_by(id=empresa_id, usuario_id=usuario.id).first()
    if not empresa:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    
    if empresa_data.razao_social is not None:
        empresa.razao_social = empresa_data.razao_social
    if empresa_data.ambiente is not None:
        empresa.ambiente = empresa_data.ambiente
    if empresa_data.uf_autor is not None:
        empresa.uf_autor = empresa_data.uf_autor
    if empresa_data.pasta_destino is not None:
        empresa.pasta_destino = empresa_data.pasta_destino
    
    db.commit()
    db.refresh(empresa)
    
    tem_certificado = bool(empresa.certificado_base64)
    validade = None
    dias_para_vencer = None
    
    if tem_certificado:
        try:
            cert_pfx_bytes = base64.b64decode(descriptografar(empresa.certificado_base64))
            cert_senha = descriptografar(empresa.certificado_senha)
            validade, dias_para_vencer = verificar_validade_certificado(cert_pfx_bytes, cert_senha)
        except:
            pass
    
    return {
        "id": empresa.id,
        "cnpj": empresa.cnpj,
        "razao_social": empresa.razao_social,
        "ambiente": empresa.ambiente,
        "uf_autor": empresa.uf_autor,
        "ultimo_nsu": empresa.ultimo_nsu,
        "ultima_consulta": empresa.ultima_consulta,
        "bloqueio_656_ate": empresa.bloqueio_656_ate,
        "tem_certificado": tem_certificado,
        "certificado_valido_ate": validade,
        "dias_para_vencer": dias_para_vencer
    }

def _normalizar_cnpj_14(cnpj_ou_cpf: str) -> str:
    """Retorna apenas dígitos, 14 caracteres (zeros à esquerda)."""
    if not cnpj_ou_cpf:
        return ""
    num = re.sub(r"\D", "", str(cnpj_ou_cpf))
    return num.zfill(14)[:14]


def _parse_data_emissao(val) -> Optional[datetime]:
    """Converte string ISO ou outro formato para datetime, ou retorna None."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    s = str(val).strip()
    if not s:
        return None
    try:
        if "T" in s:
            return datetime.fromisoformat(s.replace("Z", "+00:00")[:19])
        return datetime.strptime(s[:10], "%Y-%m-%d")
    except Exception:
        return None


@router.post("/{empresa_id}/importar-manual")
async def importar_arquivos_locais(
    empresa_id: int,
    arquivos: List[UploadFile] = File(..., description="Arquivos XML"),
    usuario=Depends(get_usuario_atual),
    db: Session = Depends(get_db),
):
    """Importa XMLs enviados pelo usuário. Roteia cada nota pela empresa (CNPJ destinatário)."""
    from app.core.dependencies import ids_empresas_acessiveis, usuario_acessa_empresa
    if not usuario_acessa_empresa(db, usuario, empresa_id):
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    empresa_selecionada = db.query(Empresa).filter_by(id=empresa_id).first()
    if not empresa_selecionada:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")

    ids = ids_empresas_acessiveis(db, usuario)
    empresas_usuario = (
        db.query(Empresa).filter(Empresa.id.in_(ids)).all() if ids else []
    )
    cnpj_to_empresa = {}
    for emp in empresas_usuario:
        cnpj_norm = _normalizar_cnpj_14(emp.cnpj)
        if cnpj_norm:
            cnpj_to_empresa[cnpj_norm] = emp

    contagem_por_empresa = {}
    nao_pertencentes = 0
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
            logger.warning(f"Erro ao ler {arquivo.filename}: {e}")
            erros += 1
            continue

        try:
            parser = XMLParser(conteudo_xml)
            if parser.root is None:
                logger.warning(f"XML inválido ou ilegível: {arquivo.filename}")
                erros += 1
                continue
            info = parser.extrair_todos_os_dados()

            chave = re.search(r'chNFe="(\d+)"|chNFe>(\d+)<', conteudo_xml)
            chave_limpa = chave.group(1) or chave.group(2) if chave else arquivo.filename.replace(".xml", "")

            dest_cnpj = parser.extrair_destinatario_cnpj()
            if dest_cnpj is not None:
                empresa_dest = cnpj_to_empresa.get(dest_cnpj)
                if empresa_dest is None:
                    nao_pertencentes += 1
                    continue
                empresa_id_alvo = empresa_dest.id
            else:
                empresa_id_alvo = empresa_id

            nota_existente = db.query(NotaFiscal).filter_by(chave_acesso=chave_limpa).first()
            data_emissao = _parse_data_emissao(info.get("data")) or datetime.now()

            if not nota_existente:
                nova_nota = NotaFiscal(
                    empresa_id=empresa_id_alvo,
                    chave_acesso=chave_limpa,
                    numero_nfe=info.get("numero"),
                    serie=info.get("serie"),
                    emitente_nome=info.get("emitente") or "Desconhecido",
                    emitente_cnpj=info.get("emitente_cnpj"),
                    natureza_operacao=info.get("natureza_operacao"),
                    valor_total=info.get("valor"),
                    data_emissao=data_emissao,
                    xml_conteudo=conteudo_xml,
                    status_manifesto=info.get("tipo_operacao") or "COMPLETA",
                )
                db.add(nova_nota)
            else:
                nota_existente.empresa_id = empresa_id_alvo
                nota_existente.xml_conteudo = conteudo_xml
                nota_existente.numero_nfe = info.get("numero")
                nota_existente.serie = info.get("serie")
                nota_existente.emitente_nome = info.get("emitente") or nota_existente.emitente_nome or "Desconhecido"
                if info.get("emitente_cnpj"):
                    nota_existente.emitente_cnpj = info.get("emitente_cnpj")
                if info.get("natureza_operacao"):
                    nota_existente.natureza_operacao = info.get("natureza_operacao")
                nota_existente.valor_total = info.get("valor")
                nota_existente.data_emissao = data_emissao
                nota_existente.status_manifesto = info.get("tipo_operacao") or "COMPLETA"

            contagem_por_empresa[empresa_id_alvo] = contagem_por_empresa.get(empresa_id_alvo, 0) + 1
            db.commit()
        except Exception as e:
            logger.warning(f"Erro ao importar {arquivo.filename}: {e}")
            db.rollback()
            erros += 1

    importados_por_empresa = []
    for eid, qtd in contagem_por_empresa.items():
        emp = next((x for x in empresas_usuario if x.id == eid), None)
        importados_por_empresa.append({
            "empresa_id": eid,
            "razao_social": emp.razao_social if emp else "",
            "quantidade": qtd,
        })

    return {
        "status": "Processamento concluído",
        "importados_por_empresa": importados_por_empresa,
        "nao_pertencentes": nao_pertencentes,
        "erros": erros,
    }

@router.delete("/{empresa_id}", status_code=204)
async def deletar_empresa(
    empresa_id: int,
    usuario = Depends(get_usuario_atual),
    db: Session = Depends(get_db)
):
    """Deleta uma empresa."""
    empresa = db.query(Empresa).filter_by(id=empresa_id, usuario_id=usuario.id).first()
    if not empresa:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    
    db.delete(empresa)
    db.commit()
    return None

@router.post("/{empresa_id}/certificado")
async def upload_certificado(
    empresa_id: int,
    arquivo: UploadFile = File(...),
    senha: str = Form(...),
    usuario = Depends(get_usuario_atual),
    db: Session = Depends(get_db)
):
    """Faz upload do certificado digital (.pfx)."""
    empresa = db.query(Empresa).filter_by(id=empresa_id, usuario_id=usuario.id).first()
    if not empresa:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    
    # Valida extensão
    if not arquivo.filename.endswith(('.pfx', '.p12')):
        raise HTTPException(status_code=400, detail="Arquivo deve ser .pfx ou .p12")
    
    # Lê arquivo
    conteudo = await arquivo.read()
    cert_base64 = base64.b64encode(conteudo).decode()
    
    # Valida certificado
    try:
        cert_pfx_bytes = base64.b64decode(cert_base64)
        cert_valido, cnpj_base_cert = validar_cnpj_base_certificado(
            empresa.cnpj, cert_pfx_bytes, senha
        )
        
        if not cert_valido:
            # Verifica se é uma filial (mesmo CNPJ base)
            cnpj_base_empresa = extrair_cnpj_base(empresa.cnpj)
            if cnpj_base_cert and cnpj_base_cert == cnpj_base_empresa:
                # Mesmo CNPJ base - permite (é uma filial)
                logger.info(f"Certificado validado para filial | CNPJ base: {cnpj_base_cert} | Empresa: {empresa.cnpj}")
            else:
                raise HTTPException(
                    status_code=400, 
                    detail=f"CNPJ base do certificado ({cnpj_base_cert or 'N/A'}) não corresponde ao CNPJ base da empresa ({cnpj_base_empresa}). Filiais devem ter o mesmo CNPJ base."
                )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erro ao validar certificado: {str(e)}")
    
    # Criptografa e salva
    empresa.certificado_base64 = criptografar(cert_base64)
    empresa.certificado_senha = criptografar(senha)
    
    db.commit()
    
    return {"message": "Certificado salvo com sucesso"}

