import os
import time
import re
import base64
import gzip
import traceback
import uuid
import certifi
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from requests_pkcs12 import post
from requests.exceptions import RequestException, Timeout, ConnectionError, SSLError
from app.models.database import SessionLocal, Empresa, NotaFiscal, ResumoPendente, BaseConsulta, ParcelaPagamento
from app.core.security import descriptografar
from app.core.logger import setup_logger, set_empresa_log_context, clear_empresa_log_context
from app.services.suporte_logs import registrar_log_suporte
from app.services.assinatura_evento import assinar_xml_evento
from sqlalchemy.exc import IntegrityError

# Configuração de URLs por ambiente
URLS_DISTRIBUICAO = {
    '1': "https://www1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx",
    '2': "https://hom1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx"
}

URLS_EVENTO = {
    '1': "https://www1.nfe.fazenda.gov.br/NFeRecepcaoEvento4/NFeRecepcaoEvento4.asmx",
    '2': "https://hom1.nfe.fazenda.gov.br/NFeRecepcaoEvento4/NFeRecepcaoEvento4.asmx"
}

logger = setup_logger()

def _parse_data_emissao(val):
    """Converte string ISO (dhEmi) ou None para datetime. Usado para data_emissao da nota."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    if not isinstance(val, str) or not val.strip():
        return None
    try:
        # dhEmi vem como 2025-01-15T10:30:00-03:00 ou 2025-01-15T10:30:00
        return datetime.fromisoformat(val.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def validar_xml(xml_content):
    """Valida se o XML está bem formado antes de processar."""
    try:
        ET.fromstring(xml_content)
        return True
    except ET.ParseError as e:
        logger.error(f"XML malformado: {e}")
        return False

def extrair_cnpj_base(cnpj):
    """Extrai os 8 primeiros dígitos do CNPJ (CNPJ base)."""
    cnpj_limpo = "".join(filter(str.isdigit, str(cnpj)))
    return cnpj_limpo[:8] if len(cnpj_limpo) >= 8 else None

def obter_base_consulta(db, cnpj_base, ambiente):
    if not cnpj_base:
        return None
    base = db.query(BaseConsulta).filter_by(cnpj_base=cnpj_base, ambiente=ambiente).first()
    if base:
        return base
    try:
        base = BaseConsulta(cnpj_base=cnpj_base, ambiente=ambiente)
        db.add(base)
        db.commit()
        db.refresh(base)
        return base
    except IntegrityError:
        db.rollback()
        return db.query(BaseConsulta).filter_by(cnpj_base=cnpj_base, ambiente=ambiente).first()

def base_bloqueada(base_consulta):
    return bool(base_consulta and base_consulta.bloqueio_656_ate and datetime.now() < base_consulta.bloqueio_656_ate)

def obter_verify_tls():
    return os.getenv("REQUESTS_CA_BUNDLE") or os.getenv("SSL_CERT_FILE") or certifi.where()

def obter_soap_action_evento():
    return (
        os.getenv("SEFAZ_EVENTO_SOAP_ACTION")
        or "http://www.portalfiscal.inf.br/nfe/wsdl/NFeRecepcaoEvento4/nfeRecepcaoEventoNF"
    )

def extrair_status_retorno(xml_texto):
    cstat_match = re.search(r'<cStat>(\d+)</cStat>', xml_texto or '')
    xmotivo_match = re.search(r'<xMotivo>(.*?)</xMotivo>', xml_texto or '', re.DOTALL)
    return (cstat_match.group(1) if cstat_match else None,
            xmotivo_match.group(1).strip() if xmotivo_match else None)

def registrar_timeline_evento(empresa_id, cnpj_base, ambiente, ciclo_id, status, mensagem=None, extra=None):
    contexto = {
        "cnpj_base": cnpj_base,
        "ambiente": ambiente,
        "ciclo_id": ciclo_id,
        "status": status
    }
    if extra:
        contexto.update(extra)
    registrar_log_suporte(
        empresa_id=empresa_id,
        nivel="INFO",
        tipo="timeline",
        mensagem=mensagem or status,
        contexto=contexto
    )

def validar_cnpj_base_certificado(cnpj_empresa, cert_pfx_bytes, cert_senha):
    """
    Valida se o CNPJ base da empresa corresponde ao CNPJ do certificado.
    Retorna (valido, cnpj_base_certificado)
    """
    try:
        from cryptography.hazmat.primitives.serialization import pkcs12
        from cryptography.hazmat.backends import default_backend
        
        private_key, certificate, additional_certificates = pkcs12.load_key_and_certificates(
            cert_pfx_bytes, cert_senha.encode(), default_backend()
        )
        
        if certificate is None:
            logger.warning("Certificado não encontrado no arquivo PFX")
            return False, None
        
        # Extrai CNPJ do certificado (pode estar em serialNumber ou em OID específico)
        cnpj_cert = None
        
        # Tenta extrair do serialNumber (formato comum)
        for attr in certificate.subject:
            # serialNumber geralmente contém o CNPJ
            if hasattr(attr, 'oid'):
                oid_name = attr.oid._name if hasattr(attr.oid, '_name') else str(attr.oid)
                if 'serialNumber' in oid_name.lower() or '2.5.4.5' in str(attr.oid):
                    valor = str(attr.value)
                    # Remove caracteres não numéricos
                    cnpj_cert = "".join(filter(str.isdigit, valor))
                    if len(cnpj_cert) >= 14:
                        break
        
        # Se não encontrou no subject, tenta buscar em todas as extensões
        if not cnpj_cert or len(cnpj_cert) < 14:
            try:
                for ext in certificate.extensions:
                    if hasattr(ext, 'value'):
                        valor_str = str(ext.value)
                        # Procura por padrão de CNPJ (14 dígitos)
                        cnpj_match = re.search(r'\d{14}', valor_str)
                        if cnpj_match:
                            cnpj_cert = cnpj_match.group()
                            break
            except:
                pass
        
        # Se ainda não encontrou, tenta extrair do subject completo
        if not cnpj_cert or len(cnpj_cert) < 14:
            subject_str = str(certificate.subject)
            cnpj_match = re.search(r'\d{14}', subject_str)
            if cnpj_match:
                cnpj_cert = cnpj_match.group()
        
        if not cnpj_cert or len(cnpj_cert) < 14:
            logger.warning("CNPJ não encontrado no certificado digital")
            return False, None
        
        # Extrai CNPJ base (8 primeiros dígitos)
        cnpj_base_cert = cnpj_cert[:8]
        cnpj_base_empresa = extrair_cnpj_base(cnpj_empresa)
        
        if not cnpj_base_empresa:
            logger.warning(f"CNPJ da empresa inválido: {cnpj_empresa}")
            return False, None
        
        # Validação: compara apenas CNPJ base (permite filiais com mesmo CNPJ base)
        valido = cnpj_base_cert == cnpj_base_empresa
        
        if not valido:
            logger.warning(f"CNPJ base não corresponde | Empresa: {cnpj_base_empresa} | Certificado: {cnpj_base_cert}")
            logger.info(f"NOTA: Filiais podem ter o mesmo CNPJ base. Se esta é uma filial, verifique se o CNPJ base está correto.")
        
        return valido, cnpj_base_cert
        
    except Exception as e:
        logger.error(f"Erro ao validar CNPJ base do certificado: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        return False, None

def enviar_ciencia_operacao(chave, cnpj, pfx_bytes, senha, ambiente='1'):
    """Envia o evento de Ciência da Operação (210210) para liberar o XML completo."""
    cnpj_limpo = "".join(filter(str.isdigit, cnpj))
    agora = datetime.now().strftime('%Y-%m-%dT%H:%M:%S-03:00')
    id_evento = f"ID210210{chave}01"
    url_evento = URLS_EVENTO.get(ambiente, URLS_EVENTO['1'])
    
    id_lote = "1".zfill(15)
    c_orgao = "91"
    env_evento = (
        f'<envEvento xmlns="http://www.portalfiscal.inf.br/nfe" versao="1.00"><idLote>{id_lote}</idLote>'
        f'<evento versao="1.00"><infEvento Id="{id_evento}"><cOrgao>{c_orgao}</cOrgao><tpAmb>{ambiente}</tpAmb><CNPJ>{cnpj_limpo}</CNPJ>'
        f'<chNFe>{chave}</chNFe><dhEvento>{agora}</dhEvento><tpEvento>210210</tpEvento><nSeqEvento>1</nSeqEvento>'
        '<verEvento>1.00</verEvento><detEvento versao="1.00"><descEvento>Ciencia da Operacao</descEvento></detEvento></infEvento></evento>'
        '</envEvento>'
    )
    try:
        env_evento = assinar_xml_evento(env_evento, pfx_bytes, senha, incluir_declaracao=False)
        xml_evento = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<soapenv:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
            'xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
            '<soapenv:Body><nfeDadosMsg xmlns="http://www.portalfiscal.inf.br/nfe/wsdl/NFeRecepcaoEvento4">'
            f'{env_evento}'
            '</nfeDadosMsg></soapenv:Body></soapenv:Envelope>'
        )
    except Exception as e:
        logger.error(f"Erro ao assinar XML da ciência | Chave: {chave[:10]}... | Erro: {e}")
        return False

    try:
        soap_action = obter_soap_action_evento()
        response = post(
            url_evento,
            data=xml_evento.encode('utf-8'),
            headers={
                'Content-Type': 'text/xml; charset=utf-8',
                'SOAPAction': f'"{soap_action}"'
            },
            pkcs12_data=pfx_bytes,
            pkcs12_password=senha,
            timeout=60,
            verify=obter_verify_tls()
        )
        if response.status_code == 200:
            cstat, xmotivo = extrair_status_retorno(response.text)
            if cstat and cstat != '135':
                logger.warning(
                    f"Ciência retornou cStat={cstat} | Motivo: {xmotivo or 'N/A'} | Chave: {chave[:10]}..."
                )
                return False
            logger.info(f"Ciência da Operação enviada | Chave: {chave[:10]}... | CNPJ: {cnpj_limpo[:8]}...")
            return True
        else:
            cstat, xmotivo = extrair_status_retorno(response.text)
            detalhe = f" | cStat={cstat} | Motivo={xmotivo}" if cstat or xmotivo else ""
            logger.warning(
                f"Falha ao enviar ciência | Status: {response.status_code} | Chave: {chave[:10]}...{detalhe}"
            )
            logger.debug(f"Resposta ciencia (status {response.status_code}): {response.text[:800]}")
            return False
    except SSLError as e:
        logger.error(
            f"Erro TLS ao enviar ciência | Chave: {chave[:10]}... | Erro: {e} | "
            f"CA: {obter_verify_tls()}"
        )
        return False
    except Exception as e:
        logger.error(f"Erro ao enviar ciência da operação | Chave: {chave[:10]}... | Erro: {e}")
        return False

def processar_evento(xml_evento, empresa_id, db):
    """
    Processa eventos de NF-e (cancelamentos, ciência, etc).
    Retorna True se processado com sucesso.
    """
    try:
        root = ET.fromstring(xml_evento)
        ns = {'nfe': 'http://www.portalfiscal.inf.br/nfe'}
        
        # Identifica tipo de evento
        inf_evento = root.find('.//nfe:infEvento', ns)
        if inf_evento is None:
            return False
        
        tp_evento = inf_evento.findtext('nfe:tpEvento', namespaces=ns)
        ch_nfe = inf_evento.findtext('nfe:chNFe', namespaces=ns)
        
        if not ch_nfe:
            return False
        
        # Busca a nota relacionada
        nota = db.query(NotaFiscal).filter_by(chave_acesso=ch_nfe).first()
        if nota:
            # Atualiza status baseado no tipo de evento
            if tp_evento == '110111':  # Cancelamento
                nota.status_manifesto = 'CANCELADA'
                logger.info(f"Evento de cancelamento processado | Chave: {ch_nfe[:10]}...")
            elif tp_evento == '210210':  # Ciência
                logger.info(f"Evento de ciência processado | Chave: {ch_nfe[:10]}...")
            else:
                logger.info(f"Evento recebido sem tratamento específico | tpEvento: {tp_evento} | Chave: {ch_nfe[:10]}...")
            
            # Salva o XML do evento (opcional, pode criar tabela separada)
            return True

        logger.info(f"Evento recebido para nota inexistente | tpEvento: {tp_evento} | Chave: {ch_nfe[:10]}...")
        return False
    except Exception as e:
        logger.error(f"Erro ao processar evento: {e}")
        return False

def processar_retorno_sefaz(xml_retorno, empresa_id, cnpj_loja, pfx_bytes, cert_senha, ambiente):
    """
    Processa o lote de documentos e retorna
    (novo_ultNSU, maxNSU, bloqueio_656, quantidade_notas, cStat).
    """
    cnpj_limpo = "".join(filter(str.isdigit, cnpj_loja))
    db = SessionLocal()
    
    try:
        # CORREÇÃO: Extrai o conteúdo do envelope SOAP primeiro
        # A resposta vem dentro de um envelope SOAP, precisamos extrair o retDistDFeInt
        xml_conteudo = xml_retorno
        
        # Tenta extrair o conteúdo do envelope SOAP usando XML parsing
        try:
            root = ET.fromstring(xml_retorno)
            # Namespaces do SOAP e NFe
            namespaces = {
                'soap': 'http://www.w3.org/2003/05/soap-envelope',
                'soap12': 'http://www.w3.org/2003/05/soap-envelope',
                'nfe': 'http://www.portalfiscal.inf.br/nfe'
            }
            
            # Procura pelo Body do SOAP
            body = root.find('.//{http://www.w3.org/2003/05/soap-envelope}Body')
            if body is not None:
                # Procura pelo retDistDFeInt dentro do Body
                ret_dist = body.find('.//{http://www.portalfiscal.inf.br/nfe}retDistDFeInt')
                if ret_dist is not None:
                    # Converte o elemento para string XML
                    xml_conteudo = ET.tostring(ret_dist, encoding='unicode')
                    logger.debug(f"XML extraído do envelope SOAP | Tamanho: {len(xml_conteudo)} caracteres")
        except Exception as e:
            # Se falhar o parsing, usa o XML original (fallback)
            logger.warning(f"Erro ao extrair XML do envelope SOAP, usando XML completo: {e}")
            xml_conteudo = xml_retorno
        
        # 1. Extração de Status e NSUs (agora usando xml_conteudo extraído)
        # CORREÇÃO: Tenta extrair primeiro do xml_conteudo, depois do xml_retorno completo (fallback)
        cStat_match = re.search(r'<cStat>(\d+)</cStat>', xml_conteudo)
        if not cStat_match:
            # Fallback: tenta no XML completo (pode ser erro SOAP)
            cStat_match = re.search(r'<cStat>(\d+)</cStat>', xml_retorno)
        cStat = cStat_match.group(1) if cStat_match else "???"
        
        xMotivo_match = re.search(r'<xMotivo>(.*?)</xMotivo>', xml_conteudo)
        if not xMotivo_match:
            # Fallback: tenta no XML completo
            xMotivo_match = re.search(r'<xMotivo>(.*?)</xMotivo>', xml_retorno)
        xMotivo = xMotivo_match.group(1) if xMotivo_match else "Sem motivo informado"
        
        # Se ainda não encontrou, tenta com namespace
        if cStat == "???":
            cStat_match = re.search(r'<nfe:cStat>(\d+)</nfe:cStat>', xml_retorno)
            cStat = cStat_match.group(1) if cStat_match else "???"
        
        if xMotivo == "Sem motivo informado":
            xMotivo_match = re.search(r'<nfe:xMotivo>(.*?)</nfe:xMotivo>', xml_retorno)
            xMotivo = xMotivo_match.group(1) if xMotivo_match else "Sem motivo informado"
        
        # Log detalhado se não encontrou cStat
        if cStat == "???":
            logger.warning(f"Não foi possível extrair cStat do XML | Tamanho XML: {len(xml_retorno)} caracteres")
            logger.debug(f"Primeiros 1000 caracteres do XML: {xml_retorno[:1000]}")
        
        ult_nsu_match = re.search(r'<ultNSU>(\d+)</ultNSU>', xml_conteudo)
        if not ult_nsu_match:
            ult_nsu_match = re.search(r'<ultNSU>(\d+)</ultNSU>', xml_retorno)
        
        max_nsu_match = re.search(r'<maxNSU>(\d+)</maxNSU>', xml_conteudo)
        if not max_nsu_match:
            max_nsu_match = re.search(r'<maxNSU>(\d+)</maxNSU>', xml_retorno)
        
        ult_nsu = ult_nsu_match.group(1) if ult_nsu_match else None
        max_nsu = max_nsu_match.group(1) if max_nsu_match else None
        
        # Normaliza NSUs para comparação numérica (remove zeros à esquerda para comparação)
        ult_nsu_int = int(ult_nsu) if ult_nsu else None
        max_nsu_int = int(max_nsu) if max_nsu else None
        
        # Verificação de bloqueio por consumo indevido (656)
        if cStat == '656':
            ambiente_nome = 'Produção' if ambiente == '1' else 'Homologação'
            logger.warning(f"Erro 656 - Consumo Indevido detectado | Ambiente: {ambiente_nome} | CNPJ: {cnpj_limpo[:8]}... | Motivo: {xMotivo}")
            registrar_log_suporte(
                empresa_id=empresa_id,
                nivel="WARNING",
                tipo="sefaz",
                mensagem=f"Erro 656 - Consumo Indevido | Ambiente: {ambiente_nome} | Motivo: {xMotivo}",
                cstat=cStat,
                ult_nsu=ult_nsu,
                max_nsu=max_nsu
            )
            
            # Em homologação com NSU 0, pode ser normal (sem documentos disponíveis)
            empresa_temp = db.query(Empresa).filter_by(id=empresa_id).first()
            if ambiente == '2' and empresa_temp and empresa_temp.ultimo_nsu == '0':
                logger.info(f"[HOMOLOGAÇÃO] Erro 656 com NSU 0 - Pode ser normal se não houver documentos disponíveis")
            
            # Atualiza bloqueio na empresa e salva maxNSU se disponível
            empresa = db.query(Empresa).filter_by(id=empresa_id).first()
            if empresa:
                empresa.bloqueio_656_ate = datetime.now() + timedelta(hours=2)
                empresa.ultima_sync_mensagem = "Bloqueio 656 - aguardar 2h"
                base_consulta = obter_base_consulta(
                    db,
                    extrair_cnpj_base(cnpj_loja),
                    ambiente
                )
                if base_consulta:
                    base_consulta.bloqueio_656_ate = empresa.bloqueio_656_ate
                # CORREÇÃO 3: Salva maxNSU retornado apenas se for maior que o atual
                if max_nsu:
                    try:
                        max_nsu_int = int(max_nsu)
                        max_nsu_conhecido_int = int(empresa.max_nsu_conhecido) if empresa.max_nsu_conhecido and empresa.max_nsu_conhecido != '000000000000000' else 0
                        if max_nsu_int > max_nsu_conhecido_int:
                            empresa.max_nsu_conhecido = max_nsu.zfill(15)
                            logger.info(f"maxNSU conhecido atualizado (erro 656): {max_nsu} | Será usado para descobrir NSU atual após bloqueio")
                        else:
                            logger.debug(f"maxNSU ({max_nsu}) não é maior que max_nsu_conhecido atual ({empresa.max_nsu_conhecido}), mantendo valor atual")
                    except Exception as e:
                        logger.warning(f"Erro ao atualizar max_nsu_conhecido (erro 656): {e}")
                        # Se der erro, ainda assim atualiza (comportamento antigo como fallback)
                        empresa.max_nsu_conhecido = max_nsu.zfill(15)
                empresa.ultima_sync_cstat = "656"
                empresa.ultima_sync_nfe_qtd = 0
                db.commit()
                logger.info(f"Bloqueio de 2 horas aplicado | Empresa: {empresa.razao_social} | Ambiente: {ambiente_nome} | Desbloqueio: {empresa.bloqueio_656_ate.strftime('%H:%M:%S')}")
                registrar_log_suporte(
                    empresa_id=empresa.id,
                    nivel="WARNING",
                    tipo="bloqueio",
                    mensagem=f"Bloqueio 656 aplicado | Desbloqueio: {empresa.bloqueio_656_ate.strftime('%H:%M:%S')}",
                    cstat=cStat,
                    ult_nsu=ult_nsu,
                    max_nsu=max_nsu
                )
            return None, max_nsu, True, 0, "656"
        
        # Outros erros
        if cStat not in ['137', '138']:
            logger.error(f"Erro SEFAZ | cStat: {cStat} | Motivo: {xMotivo} | CNPJ: {cnpj_limpo[:8]}...")
            registrar_log_suporte(
                empresa_id=empresa_id,
                nivel="ERROR",
                tipo="sefaz",
                mensagem=f"Erro SEFAZ | cStat: {cStat} | Motivo: {xMotivo}",
                cstat=cStat,
                ult_nsu=ult_nsu,
                max_nsu=max_nsu
            )
            return None, None, False, 0, cStat if cStat != "???" else None
        
        # Nenhum documento disponível (137)
        if cStat == '137':
            logger.info(f"Nenhum documento disponível | CNPJ: {cnpj_limpo[:8]}... | ultNSU: {ult_nsu} | maxNSU: {max_nsu}")
            # Se ultNSU >= maxNSU, significa que não há mais documentos
            if ult_nsu and max_nsu:
                ult_nsu_int = int(ult_nsu)
                max_nsu_int = int(max_nsu)
                if ult_nsu_int >= max_nsu_int:
                    logger.info(f"Fim da fila confirmado (status 137) | ultNSU: {ult_nsu} >= maxNSU: {max_nsu}")
            return ult_nsu, max_nsu, False, 0, "137"
        
        # Processamento do lote de documentos (138)
        # CORREÇÃO: Usa parsing XML adequado com namespaces para encontrar docZip
        doczips = []
        
        try:
            # Tenta usar XML parsing primeiro (mais robusto)
            root_doc = ET.fromstring(xml_conteudo)
            namespaces_doc = {
                'nfe': 'http://www.portalfiscal.inf.br/nfe'
            }
            
            # Procura por loteDistDFeInt
            lote_dist = root_doc.find('.//{http://www.portalfiscal.inf.br/nfe}loteDistDFeInt')
            if lote_dist is not None:
                # Procura todos os docZip dentro do lote
                doczips_xml = lote_dist.findall('.//{http://www.portalfiscal.inf.br/nfe}docZip')
                for doczip in doczips_xml:
                    nsu_attr = doczip.get('NSU')
                    conteudo = doczip.text if doczip.text else ''
                    if nsu_attr and conteudo:
                        doczips.append((nsu_attr, conteudo))
                logger.info(f"Documentos encontrados via XML parsing | Total: {len(doczips)}")
        except Exception as e:
            logger.warning(f"Erro no parsing XML, usando regex como fallback: {e}")
        
        # Fallback: Se não encontrou via XML parsing, usa regex
        if len(doczips) == 0:
            # Regex melhorado: procura docZip com ou sem namespace
            lote_regex = re.findall(r'<(?:\w+:)?docZip\s+NSU=["\']?(\d+)["\']?\s*>(.*?)</(?:\w+:)?docZip>', xml_conteudo, re.DOTALL | re.IGNORECASE)
            if lote_regex:
                doczips = lote_regex
                logger.info(f"Documentos encontrados via regex | Total: {len(doczips)}")
        
        lote = doczips
        logger.info(f"Lote recebido | CNPJ: {cnpj_limpo[:8]}... | Documentos: {len(lote)} | ultNSU: {ult_nsu} | maxNSU: {max_nsu}")
        
        # Log detalhado para debug
        if len(lote) == 0:
            logger.warning(f"Nenhum docZip encontrado no XML | Tamanho XML: {len(xml_conteudo)} caracteres")
            # Salva XML para debug (primeiros 2000 caracteres)
            logger.debug(f"Primeiros 2000 caracteres do XML: {xml_conteudo[:2000]}")
        
        # Se lote está vazio mas temos maxNSU, verifica se atingiu o fim
        if len(lote) == 0 and ult_nsu and max_nsu:
            ult_nsu_int = int(ult_nsu)
            max_nsu_int = int(max_nsu)
            if ult_nsu_int >= max_nsu_int:
                logger.info(f"Fim da fila confirmado (lote vazio) | ultNSU: {ult_nsu} >= maxNSU: {max_nsu}")
                return ult_nsu, max_nsu, False, 0, cStat
        
        documentos_processados = 0
        eventos_processados = 0
        
        for nsu_doc, conteudo_base64 in lote:
            try:
                # Descompacta o XML
                xml_descompactado = gzip.decompress(base64.b64decode(conteudo_base64)).decode('utf-8')
                
                # Valida XML antes de processar
                if not validar_xml(xml_descompactado):
                    logger.warning(f"XML inválido ignorado | NSU: {nsu_doc}")
                    continue
                
                # Identifica tipo de documento
                tipo = "eventos"
                if "<nfeProc" in xml_descompactado or "<procNFe" in xml_descompactado:
                    tipo = "completos"
                elif "<resNFe" in xml_descompactado:
                    tipo = "resumos"
                
                # Extrai chave de acesso
                chave_match = re.search(r'chNFe="(\d+)"|chNFe="(\d+)"|chNFe>(\d+)<', xml_descompactado)
                chave = None
                if chave_match:
                    chave = chave_match.group(1) or chave_match.group(2) or chave_match.group(3)
                
                if not chave and tipo != "eventos":
                    logger.warning(f"Chave de acesso não encontrada | NSU: {nsu_doc} | Tipo: {tipo}")
                    continue
                
                # Salva XML em disco (opcional; falha não impede gravar no banco)
                caminho = f"data/xmls/{cnpj_limpo}/{tipo}"
                try:
                    os.makedirs(caminho, exist_ok=True)
                    nome_arquivo = chave if chave else f"EVENTO_{nsu_doc}"
                    with open(f"{caminho}/{nome_arquivo}.xml", "w", encoding="utf-8") as f:
                        f.write(xml_descompactado)
                except OSError as e:
                    logger.warning(f"XML não salvo em disco (permissão/disk?) | NSU: {nsu_doc} | Erro: {e}")
                
                # Processa eventos
                if tipo == "eventos":
                    if processar_evento(xml_descompactado, empresa_id, db):
                        eventos_processados += 1
                    continue
                
                # Se for resumo, registra para processamento assíncrono (ciência será enviada depois)
                if tipo == "resumos" and chave:
                    # Verifica se já existe registro de resumo pendente
                    resumo_pendente = db.query(ResumoPendente).filter_by(
                        empresa_id=empresa_id,
                        chave_acesso=chave
                    ).first()
                    
                    if not resumo_pendente:
                        # Cria registro de resumo pendente
                        # Aguarda 5 minutos antes de tentar buscar XML completo (tempo para SEFAZ processar ciência)
                        proxima_tentativa = datetime.now() + timedelta(minutes=5)
                        resumo_pendente = ResumoPendente(
                            empresa_id=empresa_id,
                            chave_acesso=chave,
                            data_proxima_tentativa=proxima_tentativa,
                            status='pendente'
                        )
                        db.add(resumo_pendente)
                        db.flush()
                        logger.info(f"Resumo pendente registrado | Chave: {chave[:10]}... | Próxima tentativa: {proxima_tentativa.strftime('%H:%M:%S')}")
                    else:
                        logger.debug(f"Resumo pendente já existe | Chave: {chave[:10]}...")
                
                # Extrai dados e grava no banco
                from app.services.parser import extrair_dados_xml, extrair_pagamento_xml
                info = extrair_dados_xml(xml_descompactado)
                
                try:
                    nota = db.query(NotaFiscal).filter_by(chave_acesso=chave).first()
                    if not nota:
                        data_emi = _parse_data_emissao(info.get("data"))
                        db.add(NotaFiscal(
                            empresa_id=empresa_id,
                            chave_acesso=chave,
                            numero_nfe=info.get("numero"),
                            serie=info.get("serie"),
                            emitente_nome=info.get("emitente", "Desconhecido"),
                            emitente_cnpj=info.get("emitente_cnpj"),
                            natureza_operacao=info.get("natureza_operacao"),
                            valor_total=info.get("valor", 0),
                            data_emissao=data_emi or datetime.now(),
                            xml_conteudo=xml_descompactado,
                            status_manifesto="RESUMO" if tipo == "resumos" else "COMPLETA"
                        ))
                        db.flush()
                        documentos_processados += 1
                        logger.info(f"Nota salva | Tipo: {tipo} | Chave: {chave[:10]}... | Emitente: {info.get('emitente', 'N/A')[:30]}")
                    elif tipo == "completos":
                        # Atualiza nota resumo para completa (inclui data de emissão do XML)
                        nota.xml_conteudo = xml_descompactado
                        nota.numero_nfe = info.get("numero")
                        nota.serie = info.get("serie")
                        nota.emitente_nome = info.get("emitente", nota.emitente_nome)
                        if info.get("emitente_cnpj"):
                            nota.emitente_cnpj = info.get("emitente_cnpj")
                        if info.get("natureza_operacao"):
                            nota.natureza_operacao = info.get("natureza_operacao")
                        if info.get("valor") is not None:
                            nota.valor_total = info.get("valor")
                        nota.status_manifesto = "COMPLETA"
                        data_emi = _parse_data_emissao(info.get("data"))
                        if data_emi is not None:
                            nota.data_emissao = data_emi
                        db.flush()
                        documentos_processados += 1
                        logger.info(f"Nota atualizada para COMPLETA | Chave: {chave[:10]}...")
                    # Sincroniza parcelas de pagamento quando a nota está completa
                    nota_ref = db.query(NotaFiscal).filter_by(chave_acesso=chave).first()
                    if nota_ref and tipo == "completos" and nota_ref.xml_conteudo:
                        try:
                            pay = extrair_pagamento_xml(nota_ref.xml_conteudo)
                            db.query(ParcelaPagamento).filter(ParcelaPagamento.nota_id == nota_ref.id).delete()
                            for p in pay.get("parcelas") or []:
                                db.add(ParcelaPagamento(
                                    nota_id=nota_ref.id,
                                    numero_parcela=p.get("numero_parcela"),
                                    data_vencimento=p.get("data_vencimento"),
                                    valor=p.get("valor", 0),
                                    forma_pagamento=p.get("forma_pagamento"),
                                    forma_pagamento_label=p.get("forma_pagamento_label"),
                                    ind_pag=p.get("ind_pag"),
                                    data_pagamento=p.get("data_pagamento"),
                                    origem_nfe=bool(p.get("origem_nfe", True)),
                                    editavel=bool(p.get("editavel", False)),
                                ))
                        except Exception as e:
                            logger.warning(f"Erro ao sincronizar parcelas da nota | Chave: {chave[:10]}... | Erro: {e}")
                except IntegrityError as e:
                    db.rollback()
                    logger.warning(f"Nota duplicada ou erro de integridade | Chave: {chave[:10]}... | Erro: {str(e)[:100]}")
                except Exception as e:
                    db.rollback()
                    logger.error(f"Erro ao salvar nota | Chave: {chave[:10]}... | Erro: {e}")
            
            except Exception as e:
                logger.error(f"Erro ao processar documento | NSU: {nsu_doc} | Erro: {e}")
                continue
        
        db.commit()
        logger.info(f"Lote processado | Documentos: {documentos_processados} | Eventos: {eventos_processados} | CNPJ: {cnpj_limpo[:8]}...")
        
        return ult_nsu, max_nsu, False, documentos_processados, cStat
        
    except Exception as e:
        logger.error(f"Erro crítico no processamento | CNPJ: {cnpj_limpo[:8]}... | Erro: {e}\n{traceback.format_exc()}")
        db.rollback()
        return None, None, False, 0, None
    finally:
        db.close()

def recuperar_nsu_automaticamente(empresa_atual, pfx_bytes, cert_senha, ambiente, uf_autor, motivo="problema_detectado"):
    """
    Recupera NSU automaticamente quando detecta problemas.
    Esta função é chamada automaticamente pelo sistema quando detecta que NSU pode estar desatualizado.
    
    Args:
        empresa_atual: Objeto Empresa
        pfx_bytes: Bytes do certificado
        cert_senha: Senha do certificado
        ambiente: Ambiente (1=Produção, 2=Homologação)
        uf_autor: UF autorizadora
        motivo: Motivo da recuperação (para log)
    
    Returns:
        (sucesso, nsu_encontrado, mensagem)
    """
    logger.info("=" * 80)
    logger.info(f"INICIANDO RECUPERAÇÃO AUTOMÁTICA DE NSU")
    logger.info(f"Motivo: {motivo}")
    logger.info(f"Empresa: {empresa_atual.razao_social}")
    logger.info(f"CNPJ: {empresa_atual.cnpj[:8]}...")
    logger.info(f"Último NSU: {empresa_atual.ultimo_nsu}")
    logger.info(f"Max NSU Conhecido: {empresa_atual.max_nsu_conhecido}")
    logger.info("=" * 80)
    
    # Verifica se empresa está bloqueada
    if empresa_atual.bloqueio_656_ate and datetime.now() < empresa_atual.bloqueio_656_ate:
        tempo_restante = empresa_atual.bloqueio_656_ate - datetime.now()
        minutos = int(tempo_restante.total_seconds() / 60)
        logger.warning(f"Empresa bloqueada por 656 - não é possível recuperar NSU agora | Aguarde: {minutos} minutos")
        return False, None, f"Empresa bloqueada. Aguarde {minutos} minutos."
    
    # Tenta descobrir NSU atual
    try:
        nsu_atual_encontrado, _max_nsu, sucesso = descobrir_nsu_atual(
            empresa_atual, pfx_bytes, cert_senha, ambiente, uf_autor
        )
        
        if sucesso and nsu_atual_encontrado:
            logger.info(f"✓ RECUPERAÇÃO AUTOMÁTICA BEM-SUCEDIDA")
            logger.info(f"  NSU anterior: {empresa_atual.ultimo_nsu}")
            logger.info(f"  NSU encontrado: {nsu_atual_encontrado}")
            logger.info("=" * 80)
            return True, nsu_atual_encontrado, f"NSU recuperado automaticamente: {nsu_atual_encontrado}"
        else:
            logger.warning(f"✗ Não foi possível recuperar NSU automaticamente")
            logger.warning(f"  Motivo: Erro ao descobrir NSU atual")
            logger.warning("=" * 80)
            return False, None, "Não foi possível recuperar NSU automaticamente. Sistema tentará novamente mais tarde."
    except Exception as e:
        logger.error(f"Erro ao tentar recuperar NSU automaticamente: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False, None, f"Erro ao recuperar NSU: {str(e)}"

def descobrir_nsu_atual(empresa_atual, pfx_bytes, cert_senha, ambiente, uf_autor):
    """
    Descobre o NSU atual da SEFAZ quando o NSU no banco está desatualizado (ex.: após bloqueio 656).
    Usa sempre o último NSU conhecido (max_nsu_conhecido ou ultimo_nsu); não recuar no NSU evita
    novo erro 656 (Consumo Indevido). Retorna (nsu_atual_encontrado, max_nsu_resposta, sucesso).
    O chamador deve persistir max_nsu_resposta em empresa_atual.max_nsu_conhecido para evitar
    consulta leve duplicada com NSU antigo.
    """
    url_distribuicao = URLS_DISTRIBUICAO.get(ambiente, URLS_DISTRIBUICAO['1'])
    
    # Regra Receita: usar ultNSU nas solicitações subsequentes. Não pedir NSU anterior ao último utilizado.
    try:
        if empresa_atual.max_nsu_conhecido:
            nsu_para_consulta = str(int(empresa_atual.max_nsu_conhecido)).zfill(15)
            logger.info(f"Descobrindo NSU atual usando maxNSU conhecido | maxNSU: {empresa_atual.max_nsu_conhecido} | Consultando: {nsu_para_consulta}")
        elif empresa_atual.ultimo_nsu:
            nsu_para_consulta = str(int(empresa_atual.ultimo_nsu)).zfill(15)
            logger.info(f"Descobrindo NSU atual usando ultimo_nsu | ultimo_nsu: {empresa_atual.ultimo_nsu} | Consultando: {nsu_para_consulta}")
        else:
            nsu_para_consulta = '000000000000000'
            logger.info(f"Descobrindo NSU atual usando NSU 0 (sem histórico)")
    except Exception as e:
        logger.warning(f"Erro ao obter NSU para descoberta: {e}")
        nsu_para_consulta = '000000000000000'
    
    # Limpa CNPJ (remove formatação: pontos, barras, hífens)
    cnpj_limpo = "".join(filter(str.isdigit, str(empresa_atual.cnpj)))
    
    # Monta requisição
    xml_request = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap12:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">'
        '<soap12:Body><nfeDistDFeInteresse xmlns="http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe">'
        '<nfeDadosMsg><distDFeInt xmlns="http://www.portalfiscal.inf.br/nfe" versao="1.01">'
        f'<tpAmb>{ambiente}</tpAmb><cUFAutor>{uf_autor}</cUFAutor><CNPJ>{cnpj_limpo}</CNPJ>'
        f'<distNSU><ultNSU>{nsu_para_consulta}</ultNSU></distNSU>'
        '</distDFeInt></nfeDadosMsg></nfeDistDFeInteresse></soap12:Body></soap12:Envelope>'
    )
    
    try:
        response, sucesso = fazer_requisicao_com_retry(
            url_distribuicao,
            xml_request.encode('utf-8'),
            {'Content-Type': 'application/soap+xml; charset=utf-8'},
            pfx_bytes,
            cert_senha,
            max_tentativas=2,  # Menos tentativas para descoberta
            timeout=60
        )
        
        if not sucesso or not response:
            logger.error(f"Falha ao descobrir NSU atual | Empresa: {empresa_atual.razao_social}")
            return None, None, False
        
        # Extrai maxNSU do retorno (mesmo que não tenha documentos)
        max_nsu_match = re.search(r'<maxNSU>(\d+)</maxNSU>', response.text)
        cStat_match = re.search(r'<cStat>(\d+)</cStat>', response.text)
        cStat = cStat_match.group(1) if cStat_match else None
        
        if max_nsu_match:
            max_nsu = max_nsu_match.group(1)
            # O ultNSU atual será o maxNSU (ou próximo dele)
            # Se retornou 137 (nenhum documento), o ultNSU é igual ao maxNSU
            if cStat == '137':
                nsu_atual = max_nsu
                logger.info(f"NSU atual descoberto | maxNSU: {max_nsu} | Nenhum documento disponível | Novo ultNSU: {nsu_atual}")
                return nsu_atual, max_nsu, True
            # Se retornou 138 (tem documentos), extrai o ultNSU do retorno
            elif cStat == '138':
                ult_nsu_match = re.search(r'<ultNSU>(\d+)</ultNSU>', response.text)
                if ult_nsu_match:
                    nsu_atual = ult_nsu_match.group(1)
                    logger.info(f"NSU atual descoberto | ultNSU: {nsu_atual} | maxNSU: {max_nsu}")
                    return nsu_atual, max_nsu, True
        elif cStat == '656':
            logger.warning("=" * 80)
            logger.warning(f"ERRO 656 AO TENTAR DESCOBRIR NSU ATUAL")
            logger.warning("=" * 80)
            logger.warning(f"Empresa: {empresa_atual.razao_social}")
            logger.warning(f"CNPJ: {empresa_atual.cnpj[:8]}...")
            logger.warning(f"NSU usado na descoberta: {nsu_para_consulta}")
            logger.warning(f"Motivo SEFAZ: Consumo Indevido (erro 656)")
            logger.warning("=" * 80)
            logger.warning("SOLUCAO: Aguarde 2 horas antes de tentar novamente")
            logger.warning("=" * 80)
            return None, None, False
        
        logger.warning(f"Não foi possível descobrir NSU atual | cStat: {cStat} | Empresa: {empresa_atual.razao_social}")
        return None, None, False
        
    except Exception as e:
        logger.error(f"Erro ao descobrir NSU atual | Empresa: {empresa_atual.razao_social} | Erro: {e}")
        return None, None, False

def consultar_max_nsu(empresa_atual, pfx_bytes, cert_senha, ambiente, uf_autor, base_consulta=None):
    """
    Consulta leve para atualizar maxNSU no fim da fila.
    Usa sempre o último NSU conhecido (max_nsu_conhecido ou ultimo_nsu); não recua no NSU
    para evitar erro 656 (Consumo Indevido). cStat 138 = documentos novos (resposta contém docZips);
    137 = sem novidades (respeitar intervalo de 1h antes de nova verificação).
    Retorna (max_nsu, ult_nsu, cstat, xml_retorno_ou_none).
    Quando cstat é 138, xml_retorno_ou_none é o XML da resposta para processar os docZips no chamador.
    Em caso de falha retorna (None, None, None, None).
    """
    url_distribuicao = URLS_DISTRIBUICAO.get(ambiente, URLS_DISTRIBUICAO['1'])
    cnpj_limpo = "".join(filter(str.isdigit, str(empresa_atual.cnpj)))

    try:
        if base_consulta and base_consulta.max_nsu_cache and base_consulta.max_nsu_cache_expira_em:
            if datetime.now() < base_consulta.max_nsu_cache_expira_em and base_consulta.max_nsu_cache_cnpj == empresa_atual.cnpj:
                logger.info(
                    f"Consulta leve maxNSU usando cache | maxNSU: {base_consulta.max_nsu_cache} | "
                    f"CNPJ: {empresa_atual.cnpj[:8]}..."
                )
                return base_consulta.max_nsu_cache, empresa_atual.ultimo_nsu, "CACHE", None

        if empresa_atual.max_nsu_conhecido:
            base_nsu = int(empresa_atual.max_nsu_conhecido)
        else:
            base_nsu = int(empresa_atual.ultimo_nsu or '0')
    except Exception:
        base_nsu = 0

    # Regra Receita: usar ultNSU nas solicitações subsequentes. Não pedir NSU anterior ao último utilizado.
    nsu_para_consulta = str(base_nsu).zfill(15)

    xml_request = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap12:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">'
        '<soap12:Body><nfeDistDFeInteresse xmlns="http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe">'
        '<nfeDadosMsg><distDFeInt xmlns="http://www.portalfiscal.inf.br/nfe" versao="1.01">'
        f'<tpAmb>{ambiente}</tpAmb><cUFAutor>{uf_autor}</cUFAutor><CNPJ>{cnpj_limpo}</CNPJ>'
        f'<distNSU><ultNSU>{nsu_para_consulta}</ultNSU></distNSU>'
        '</distDFeInt></nfeDadosMsg></nfeDistDFeInteresse></soap12:Body></soap12:Envelope>'
    )

    try:
        response, sucesso = fazer_requisicao_com_retry(
            url_distribuicao,
            xml_request.encode('utf-8'),
            {'Content-Type': 'application/soap+xml; charset=utf-8'},
            pfx_bytes,
            cert_senha,
            max_tentativas=2,
            timeout=60
        )

        if not sucesso or not response:
            logger.warning(f"Falha na consulta leve de maxNSU | Empresa: {empresa_atual.razao_social}")
            return None, None, None, None

        xml_retorno = response.text
        cstat_match = re.search(r'<cStat>(\d+)</cStat>', xml_retorno)
        cstat = cstat_match.group(1) if cstat_match else None
        ult_match = re.search(r'<ultNSU>(\d+)</ultNSU>', xml_retorno)
        max_match = re.search(r'<maxNSU>(\d+)</maxNSU>', xml_retorno)
        ult_nsu = ult_match.group(1) if ult_match else None
        max_nsu = max_match.group(1) if max_match else None

        logger.info(f"Consulta leve maxNSU | cStat: {cstat} | ultNSU: {ult_nsu} | maxNSU: {max_nsu} | Empresa: {empresa_atual.razao_social}")
        if base_consulta and max_nsu:
            base_consulta.max_nsu_cache = max_nsu
            base_consulta.max_nsu_cache_cnpj = empresa_atual.cnpj
            base_consulta.max_nsu_cache_expira_em = datetime.now() + timedelta(minutes=15)
        # Quando cStat 138 a resposta contém docZips; retornar o XML para o chamador processar
        xml_para_processar = xml_retorno if cstat == '138' else None
        return max_nsu, ult_nsu, cstat, xml_para_processar
    except Exception as e:
        logger.warning(f"Erro na consulta leve de maxNSU | Empresa: {empresa_atual.razao_social} | Erro: {e}")
        return None, None, None, None

def fazer_requisicao_com_retry(url, data, headers, pkcs12_data, pkcs12_password, max_tentativas=3, timeout=60):
    """
    Faz requisição com retry e backoff exponencial.
    Retorna (response, sucesso)
    """
    for tentativa in range(1, max_tentativas + 1):
        try:
            response = post(
                url,
                data=data,
                headers=headers,
                pkcs12_data=pkcs12_data,
                pkcs12_password=pkcs12_password,
                timeout=timeout,
                verify=obter_verify_tls()
            )
            
            if response.status_code == 200:
                return response, True
            
            # Log mais detalhado para debug
            logger.warning(f"Requisição retornou status {response.status_code} | Tentativa: {tentativa}/{max_tentativas} | URL: {url}")
            
            # Se for 404, loga o conteúdo da resposta para debug
            if response.status_code == 404:
                logger.error(f"Erro 404 - URL não encontrada | URL: {url}")
                logger.error(f"Resposta (primeiros 500 chars): {response.text[:500]}")
                # Não tenta novamente se for 404 - é erro de configuração
                return None, False
            
        except SSLError as e:
            logger.error(f"Erro TLS na requisição | Erro: {e} | CA: {obter_verify_tls()}")
            return None, False
        except Timeout:
            logger.warning(f"Timeout na requisição | Tentativa: {tentativa}/{max_tentativas}")
            if tentativa < max_tentativas:
                tempo_espera = 2 ** tentativa  # Backoff exponencial: 2s, 4s, 8s
                time.sleep(tempo_espera)
            else:
                logger.error("Timeout após todas as tentativas")
                return None, False
                
        except ConnectionError:
            logger.warning(f"Erro de conexão | Tentativa: {tentativa}/{max_tentativas}")
            if tentativa < max_tentativas:
                tempo_espera = 2 ** tentativa
                time.sleep(tempo_espera)
            else:
                logger.error("Erro de conexão após todas as tentativas")
                return None, False
                
        except RequestException as e:
            logger.error(f"Erro na requisição | Tentativa: {tentativa}/{max_tentativas} | Erro: {e} | Tipo: {type(e).__name__}")
            # Se for 404, não tenta novamente
            if hasattr(e, 'response') and e.response and e.response.status_code == 404:
                logger.error(f"Erro 404 confirmado - URL não encontrada | URL: {url}")
                return None, False
            if tentativa < max_tentativas:
                tempo_espera = 2 ** tentativa
                time.sleep(tempo_espera)
            else:
                return None, False
                
        except Exception as e:
            error_msg = str(e) if str(e) else f"Exceção do tipo {type(e).__name__}"
            logger.error(f"Erro inesperado na requisição | Tentativa: {tentativa}/{max_tentativas} | Erro: {error_msg}")
            # Loga traceback completo para debug
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return None, False
    
    return None, False

def executar_consulta(empresa_id: int = None):
    """
    Função principal de sincronização com todas as melhorias implementadas.
    
    Args:
        empresa_id: ID da empresa específica para sincronizar. Se None, sincroniza todas (não recomendado).
    """
    logger.info("=" * 80)
    logger.info("INÍCIO DO CICLO DE SINCRONIZAÇÃO")
    logger.info("=" * 80)
    
    db = SessionLocal()
    try:
        grupos_empresas = []
        sync_somente_empresa = os.getenv("WORKER_SYNC_SOMENTE_EMPRESA", "0").strip().lower() in ("1", "true", "yes")

        # Se empresa_id foi especificado, sincroniza a empresa alvo (e opcionalmente todas do mesmo CNPJ base)
        if empresa_id:
            empresa_alvo = db.query(Empresa).filter_by(id=empresa_id).first()
            if not empresa_alvo:
                logger.warning("Empresa não encontrada para sincronização")
                return
            cnpj_base = extrair_cnpj_base(empresa_alvo.cnpj)
            ambiente = empresa_alvo.ambiente
            if sync_somente_empresa:
                empresas_base = [empresa_alvo]
                grupos_empresas = [empresas_base]
                logger.info(f"Sync somente empresa (modo teste) | ID: {empresa_id} | Nome: {empresa_alvo.razao_social} | Base: {cnpj_base} | Total: 1")
            else:
                empresas_base = db.query(Empresa).filter(
                    Empresa.cnpj.like(f"{cnpj_base}%"),
                    Empresa.ambiente == ambiente
                ).order_by(Empresa.id.asc()).all()
                grupos_empresas = [empresas_base]
                logger.info(f"Sincronizando fila única por CNPJ base | ID: {empresa_id} | Base: {cnpj_base} | Total: {len(empresas_base)}")
        else:
            # Se não especificado, sincroniza todas as empresas agrupadas por CNPJ base
            empresas = db.query(Empresa).all()
            if not empresas:
                logger.warning("Nenhuma empresa encontrada para sincronização")
                return
            logger.warning("ATENÇÃO: Sincronizando TODAS as empresas agrupadas por CNPJ base. Isso pode causar bloqueios!")

            grupos_map = {}
            for empresa in empresas:
                cnpj_base = extrair_cnpj_base(empresa.cnpj)
                chave = f"{cnpj_base}:{empresa.ambiente}"
                grupos_map.setdefault(chave, []).append(empresa)
            grupos_empresas = list(grupos_map.values())

        total_empresas = sum(len(grupo) for grupo in grupos_empresas)
        logger.info(f"Total de empresas para sincronizar: {total_empresas} | Grupos por base: {len(grupos_empresas)}")
        if empresa_id and sync_somente_empresa:
            logger.info("Modo ativo: sync somente empresa (WORKER_SYNC_SOMENTE_EMPRESA=1)")
        elif empresa_id:
            logger.info("Modo ativo: sync por CNPJ base (todas as empresas do mesmo base)")

        for grupo in grupos_empresas:
            if not grupo:
                continue
            cnpj_base = extrair_cnpj_base(grupo[0].cnpj)
            ambiente_base = grupo[0].ambiente
            base_consulta = obter_base_consulta(db, cnpj_base, ambiente_base)
            ciclo_id = uuid.uuid4().hex
            interromper_grupo = False

            if base_consulta and base_consulta.processamento_ate and datetime.now() < base_consulta.processamento_ate:
                logger.warning(f"Fila base em processamento - pulando grupo | Base: {cnpj_base} | Ambiente: {ambiente_base}")
                registrar_timeline_evento(
                    empresa_id=grupo[0].id,
                    cnpj_base=cnpj_base,
                    ambiente=ambiente_base,
                    ciclo_id=ciclo_id,
                    status="base_em_processamento",
                    mensagem="Fila base em processamento - grupo ignorado"
                )
                continue

            if base_bloqueada(base_consulta):
                tempo_restante = base_consulta.bloqueio_656_ate - datetime.now()
                minutos = int(tempo_restante.total_seconds() / 60)
                logger.warning(f"CNPJ base bloqueado por 656 - pulando grupo | Base: {cnpj_base} | Aguarde: {minutos} minutos")
                registrar_log_suporte(
                    empresa_id=grupo[0].id,
                    nivel="WARNING",
                    tipo="bloqueio",
                    mensagem="CNPJ base bloqueado por 656 - sincronização abortada",
                    contexto={"cnpj_base": cnpj_base, "tempo_restante_min": minutos}
                )
                registrar_timeline_evento(
                    empresa_id=grupo[0].id,
                    cnpj_base=cnpj_base,
                    ambiente=ambiente_base,
                    ciclo_id=ciclo_id,
                    status="base_bloqueada",
                    mensagem="CNPJ base bloqueado por 656 - grupo ignorado",
                    extra={"tempo_restante_min": minutos}
                )
                continue

            # Não bloquear o grupo inteiro por proxima_consulta_periodica: cada empresa respeita
            # seu próprio intervalo (ex.: matriz no fim da fila espera 1h; filial com fila grande
            # pode ser processada a cada ciclo). Assim evitamos 656 na matriz e mantemos a filial andando.
            if base_consulta:
                base_consulta.processamento_ate = datetime.now() + timedelta(minutes=10)
                db.commit()

            try:
                registrar_timeline_evento(
                    empresa_id=grupo[0].id,
                    cnpj_base=cnpj_base,
                    ambiente=ambiente_base,
                    ciclo_id=ciclo_id,
                    status="base_inicio",
                    mensagem="Início do ciclo da base"
                )
                for empresa in grupo:
                    # Cria sessão isolada para cada empresa
                    db_empresa = SessionLocal()
                    try:
                        # Recarrega empresa na nova sessão
                        empresa_atual = db_empresa.query(Empresa).filter_by(id=empresa.id).first()
                        if not empresa_atual:
                            continue
                        
                        registrar_timeline_evento(
                            empresa_id=empresa_atual.id,
                            cnpj_base=cnpj_base,
                            ambiente=ambiente_base,
                            ciclo_id=ciclo_id,
                            status="empresa_inicio",
                            mensagem="Início do ciclo da empresa"
                        )
                        
                        logger.info(f"Processando empresa | ID: {empresa_atual.id} | "
                                  f"Nome: {empresa_atual.razao_social} | CNPJ: {empresa_atual.cnpj} | "
                                  f"Status: {empresa_atual.status}")
                        set_empresa_log_context(empresa_atual.id, empresa_atual.cnpj)
                        logger.info("=" * 80)
                        logger.info("INÍCIO DO CICLO DA EMPRESA")
                        logger.info("=" * 80)
                        ult_inicio, max_inicio = None, None

                        # CORREÇÃO CRÍTICA: Verifica bloqueio 656 ANTES de qualquer processamento
                        # Isso evita que o worker processe empresa bloqueada mesmo se a rota /sync permitir
                        if empresa_atual.bloqueio_656_ate and datetime.now() < empresa_atual.bloqueio_656_ate:
                            tempo_restante = empresa_atual.bloqueio_656_ate - datetime.now()
                            minutos = int(tempo_restante.total_seconds() / 60)
                            segundos = int(tempo_restante.total_seconds() % 60)
                            logger.warning(f"Empresa bloqueada por 656 - abortando sincronização imediatamente | "
                                         f"Empresa: {empresa_atual.razao_social} | "
                                         f"CNPJ: {empresa_atual.cnpj[:8]}... | "
                                         f"Aguarde: {minutos} minutos e {segundos} segundos")
                            registrar_log_suporte(
                                empresa_id=empresa_atual.id,
                                nivel="WARNING",
                                tipo="bloqueio",
                                mensagem="Empresa bloqueada por 656 - sincronização abortada",
                                contexto={"tempo_restante_min": minutos}
                            )
                            registrar_timeline_evento(
                                empresa_id=empresa_atual.id,
                                cnpj_base=cnpj_base,
                                ambiente=ambiente_base,
                                ciclo_id=ciclo_id,
                                status="empresa_bloqueada_656",
                                mensagem="Empresa bloqueada por 656 - sincronização abortada",
                                extra={"tempo_restante_min": minutos}
                            )
                            if base_consulta and (
                                not base_consulta.bloqueio_656_ate
                                or base_consulta.bloqueio_656_ate < empresa_atual.bloqueio_656_ate
                            ):
                                base_consulta.bloqueio_656_ate = empresa_atual.bloqueio_656_ate
                                db.commit()
                            interromper_grupo = True
                            break  # Para o grupo inteiro para evitar nova consulta pela base

                        # Bloqueio manual aplicado pelo suporte
                        if empresa_atual.bloqueio_manual_ate and datetime.now() < empresa_atual.bloqueio_manual_ate:
                            tempo_restante = empresa_atual.bloqueio_manual_ate - datetime.now()
                            minutos = int(tempo_restante.total_seconds() / 60)
                            segundos = int(tempo_restante.total_seconds() % 60)
                            logger.warning(f"Empresa bloqueada manualmente - abortando sincronização | "
                                         f"Empresa: {empresa_atual.razao_social} | "
                                         f"Aguarde: {minutos} minutos e {segundos} segundos")
                            registrar_log_suporte(
                                empresa_id=empresa_atual.id,
                                nivel="WARNING",
                                tipo="bloqueio",
                                mensagem="Bloqueio manual ativo - sincronização abortada",
                                contexto={"tempo_restante_min": minutos}
                            )
                            registrar_timeline_evento(
                                empresa_id=empresa_atual.id,
                                cnpj_base=cnpj_base,
                                ambiente=ambiente_base,
                                ciclo_id=ciclo_id,
                                status="empresa_bloqueada_manual",
                                mensagem="Empresa bloqueada manualmente - sincronização abortada",
                                extra={"tempo_restante_min": minutos}
                            )
                            continue  # Pula para próxima empresa, não processa nada
                
                        # Verifica se empresa está aprovada
                        if empresa_atual.status != 'aprovado':
                            logger.warning(f"Empresa nao aprovada | ID: {empresa_atual.id} | "
                                        f"Status: {empresa_atual.status} | Pulando sincronizacao")
                            registrar_timeline_evento(
                                empresa_id=empresa_atual.id,
                                cnpj_base=cnpj_base,
                                ambiente=ambiente_base,
                                ciclo_id=ciclo_id,
                                status="empresa_nao_aprovada",
                                mensagem="Empresa não aprovada - sincronização ignorada"
                            )
                            continue
                        
                        # Descriptografa certificado PRIMEIRO (necessário para descobrir NSU)
                        pfx_bytes = None
                        cert_senha = None
                        
                        try:
                            if not empresa_atual.certificado_base64 or not empresa_atual.certificado_senha:
                                logger.error(f"Certificado nao cadastrado | Empresa: {empresa_atual.razao_social}")
                                continue
                            
                            # Tenta descriptografar certificado
                            try:
                                cert_base64_descripto = descriptografar(empresa_atual.certificado_base64)
                            except Exception as desc_error:
                                error_msg = str(desc_error) if str(desc_error) else f"Exceção do tipo {type(desc_error).__name__}"
                                logger.error(f"Falha ao descriptografar certificado | Empresa: {empresa_atual.razao_social} | Erro: {error_msg}")
                                registrar_log_suporte(
                                    empresa_id=empresa_atual.id,
                                    nivel="ERROR",
                                    tipo="certificado",
                                    mensagem="Falha ao descriptografar certificado",
                                    contexto={"erro": error_msg}
                                )
                                if "InvalidToken" in error_msg or "InvalidToken" in str(type(desc_error).__name__):
                                    logger.error(f"PROBLEMA CRITICO: Chave de criptografia invalida!")
                                    logger.error(f"SOLUCAO: Refaça o upload do certificado pela interface web (/empresas)")
                                raise  # Re-levanta para ser capturado no except externo
                            
                            if not cert_base64_descripto:
                                logger.error(f"Certificado vazio apos descriptografia | Empresa: {empresa_atual.razao_social}")
                                continue
                            
                            try:
                                pfx_bytes = base64.b64decode(cert_base64_descripto)
                            except Exception as decode_error:
                                logger.error(f"Certificado nao e um Base64 valido | Empresa: {empresa_atual.razao_social} | Erro: {str(decode_error)}")
                                raise
                            
                            # Tenta descriptografar senha
                            try:
                                cert_senha = descriptografar(empresa_atual.certificado_senha)
                            except Exception as senha_error:
                                error_msg = str(senha_error) if str(senha_error) else f"Exceção do tipo {type(senha_error).__name__}"
                                logger.error(f"Falha ao descriptografar senha | Empresa: {empresa_atual.razao_social} | Erro: {error_msg}")
                                registrar_log_suporte(
                                    empresa_id=empresa_atual.id,
                                    nivel="ERROR",
                                    tipo="certificado",
                                    mensagem="Falha ao descriptografar senha do certificado",
                                    contexto={"erro": error_msg}
                                )
                                if "InvalidToken" in error_msg or "InvalidToken" in str(type(senha_error).__name__):
                                    logger.error(f"PROBLEMA CRITICO: Chave de criptografia invalida!")
                                    logger.error(f"SOLUCAO: Refaça o upload do certificado pela interface web (/empresas)")
                                raise
                            
                            if not pfx_bytes or not cert_senha:
                                logger.error(f"Erro ao processar certificado | Empresa: {empresa_atual.razao_social}")
                                continue
                                
                        except Exception as e:
                            error_msg = str(e) if str(e) else f"Exceção do tipo {type(e).__name__}"
                            logger.error(f"Erro ao descriptografar certificado | Empresa: {empresa_atual.razao_social} | Erro: {error_msg}")
                            import traceback
                            logger.error(f"Traceback completo: {traceback.format_exc()}")
                            
                            # Verifica se é problema de chave de criptografia
                            if "InvalidToken" in str(type(e).__name__) or "InvalidToken" in error_msg:
                                logger.error(f"PROBLEMA: Chave de criptografia invalida ou certificado foi criptografado com outra chave")
                                logger.error(f"SOLUCAO: Refaça o upload do certificado pela interface web")
                            continue
                        
                        # Valida CNPJ base do certificado
                        cnpj_valido, cnpj_base_cert = validar_cnpj_base_certificado(
                            empresa_atual.cnpj, pfx_bytes, cert_senha
                        )
                        if not cnpj_valido:
                            cnpj_base_empresa = extrair_cnpj_base(empresa_atual.cnpj)
                            logger.error(f"CNPJ base do certificado nao corresponde | "
                                       f"Empresa CNPJ Base: {cnpj_base_empresa} | "
                                       f"Certificado CNPJ Base: {cnpj_base_cert or 'N/A'} | "
                                       f"Empresa CNPJ Completo: {empresa_atual.cnpj}")
                            logger.error(f"NOTA: Filiais devem ter o mesmo CNPJ base (8 primeiros digitos)")
                            logger.error(f"SOLUCAO: Verifique se o certificado corresponde ao CNPJ base da empresa")
                            continue
                        
                        logger.info(f"Certificado validado | CNPJ Base: {cnpj_base_cert}")
                        
                        # Configura ambiente
                        ambiente = empresa_atual.ambiente or '1'
                        uf_autor = empresa_atual.uf_autor or '41'
                        url_distribuicao = URLS_DISTRIBUICAO.get(ambiente, URLS_DISTRIBUICAO['1'])
                        
                        # Verifica bloqueio por erro 656
                        if empresa_atual.bloqueio_656_ate and datetime.now() < empresa_atual.bloqueio_656_ate:
                            tempo_restante = empresa_atual.bloqueio_656_ate - datetime.now()
                            minutos = int(tempo_restante.total_seconds() / 60)
                            logger.warning(f"Empresa bloqueada por 656 | Empresa: {empresa_atual.razao_social} | "
                                         f"CNPJ: {empresa_atual.cnpj[:8]}... | Aguarde: {minutos} minutos")
                            continue
                        
                        # Verifica se certificado foi descriptografado com sucesso
                        if not pfx_bytes or not cert_senha:
                            logger.error(f"Certificado nao disponivel para processamento | Empresa: {empresa_atual.razao_social}")
                            continue
                        
                        # Limpa bloqueio se já expirou e descobre NSU atual se necessário
                        if empresa_atual.bloqueio_656_ate and datetime.now() >= empresa_atual.bloqueio_656_ate:
                            logger.info(f"Bloqueio 656 expirado | Empresa: {empresa_atual.razao_social}")
                            
                            # Verifica se o NSU foi atualizado recentemente (últimas 2 horas)
                            # Se sim, não tenta descobrir novamente para evitar múltiplas consultas
                            deve_descobrir_nsu = True
                            if empresa_atual.ultima_consulta:
                                tempo_desde_ultima_consulta = datetime.now() - empresa_atual.ultima_consulta
                                if tempo_desde_ultima_consulta.total_seconds() < 7200:  # 2 horas = 7200 segundos
                                    logger.info(f"NSU atualizado recentemente ({int(tempo_desde_ultima_consulta.total_seconds()/60)} minutos atrás) | Pulando descoberta para evitar consumo indevido | Empresa: {empresa_atual.razao_social}")
                                    deve_descobrir_nsu = False
                            
                            # Tenta descobrir o NSU atual apenas se necessário
                            descoberta_retornou_656 = False
                            if deve_descobrir_nsu:
                                logger.info(f"Tentando descobrir NSU atual apos bloqueio | Empresa: {empresa_atual.razao_social}")
                                try:
                                    nsu_atual_encontrado, max_nsu_descoberto, sucesso_descobrir = descobrir_nsu_atual(
                                        empresa_atual, pfx_bytes, cert_senha, ambiente, uf_autor
                                    )
                                    
                                    if sucesso_descobrir and nsu_atual_encontrado:
                                        nsu_atual_val = nsu_atual_encontrado.zfill(15)
                                        ultimo_nsu_antigo = empresa_atual.ultimo_nsu
                                        # SEMPRE usar o ultNSU retornado pela SEFAZ na próxima solicitação
                                        empresa_atual.ultimo_nsu = nsu_atual_val
                                        if int(nsu_atual_val) < int(ultimo_nsu_antigo or '0'):
                                            logger.info(
                                                f"ultNSU da SEFAZ (descoberta) menor que o armazenado - usando o da SEFAZ | "
                                                f"Armazenado: {ultimo_nsu_antigo} | Descoberto: {nsu_atual_val} | "
                                                f"Empresa: {empresa_atual.razao_social}"
                                            )
                                        # Persistir maxNSU da resposta para não entrar em "fim da fila" com valor antigo e disparar consulta leve duplicada (656)
                                        if max_nsu_descoberto:
                                            max_nsu_val = str(max_nsu_descoberto).zfill(15)
                                            try:
                                                atual_int = int(empresa_atual.max_nsu_conhecido or '0') if empresa_atual.max_nsu_conhecido else 0
                                                if int(max_nsu_descoberto) >= atual_int:
                                                    empresa_atual.max_nsu_conhecido = max_nsu_val
                                                    logger.info(f"max_nsu_conhecido atualizado apos descoberta | Novo maxNSU: {max_nsu_val} | Empresa: {empresa_atual.razao_social}")
                                            except (ValueError, TypeError):
                                                empresa_atual.max_nsu_conhecido = max_nsu_val
                                        empresa_atual.ultima_consulta = datetime.now()  # Atualiza timestamp
                                        empresa_atual.ultima_sync_mensagem = "Concluída"
                                        db_empresa.commit()
                                        logger.info(f"NSU atualizado apos descoberta | Novo ultNSU: {nsu_atual_encontrado} | Empresa: {empresa_atual.razao_social}")
                                    else:
                                        # Se a descoberta falhou, pode ser 656 ainda ativo
                                        # Verifica se há maxNSU conhecido para determinar se foi 656
                                        if empresa_atual.max_nsu_conhecido:
                                            logger.warning(f"Nao foi possivel descobrir NSU atual - possivel bloqueio 656 ainda ativo | "
                                                          f"Reaplicando bloqueio preventivo de 2 horas | Empresa: {empresa_atual.razao_social}")
                                            descoberta_retornou_656 = True
                                            empresa_atual.bloqueio_656_ate = datetime.now() + timedelta(hours=2)
                                            empresa_atual.ultima_sync_mensagem = "Bloqueio 656 - aguardar 2h"
                                            if base_consulta:
                                                base_consulta.bloqueio_656_ate = empresa_atual.bloqueio_656_ate
                                                db.commit()
                                            db_empresa.commit()
                                            registrar_log_suporte(
                                                empresa_id=empresa_atual.id,
                                                nivel="WARNING",
                                                tipo="bloqueio",
                                                mensagem="Descoberta de NSU falhou após bloqueio - bloqueio preventivo reaplicado",
                                                contexto={"cnpj_base": cnpj_base}
                                            )
                                            interromper_grupo = True
                                            break  # Não continua com consulta normal
                                        else:
                                            logger.warning(f"Nao foi possivel descobrir NSU atual | Continuando com NSU do banco | Empresa: {empresa_atual.razao_social}")
                                except Exception as e:
                                    logger.error(f"Erro ao descobrir NSU atual | Empresa: {empresa_atual.razao_social} | Erro: {str(e)}")
                                    # Se houve exceção e havia bloqueio antes, reaplica bloqueio preventivo
                                    if empresa_atual.max_nsu_conhecido:
                                        logger.warning(f"Erro na descoberta de NSU - reaplicando bloqueio preventivo | Empresa: {empresa_atual.razao_social}")
                                        descoberta_retornou_656 = True
                                        empresa_atual.bloqueio_656_ate = datetime.now() + timedelta(hours=2)
                                        empresa_atual.ultima_sync_mensagem = "Bloqueio 656 - aguardar 2h"
                                        if base_consulta:
                                            base_consulta.bloqueio_656_ate = empresa_atual.bloqueio_656_ate
                                            db.commit()
                                        db_empresa.commit()
                                        interromper_grupo = True
                                        break  # Não continua com consulta normal
                                    else:
                                        logger.warning(f"Continuando com NSU do banco | Empresa: {empresa_atual.razao_social}")
                            else:
                                logger.info(f"Usando NSU atual do banco sem tentar descobrir novamente | ultNSU: {empresa_atual.ultimo_nsu} | Empresa: {empresa_atual.razao_social}")
                            
                            # Só limpa bloqueio se a descoberta foi bem-sucedida ou não foi tentada
                            if not descoberta_retornou_656:
                                empresa_atual.bloqueio_656_ate = None
                                db_empresa.commit()
                        
                        # Loop de consultas sequenciais
                        ult_inicio = (empresa_atual.ultimo_nsu or "000000000000000").zfill(15)
                        max_inicio = (empresa_atual.max_nsu_conhecido or "000000000000000").zfill(15)
                        ciclo = 0
                        max_ciclos_base = int(os.getenv("WORKER_MAX_CICLOS", "100"))
                        # Se há muitos docs pendentes (ultNSU << maxNSU), permite mais ciclos para alcançar em uma corrida
                        max_nsu_c = int(empresa_atual.max_nsu_conhecido or "0") if empresa_atual.max_nsu_conhecido else 0
                        ult_nsu_c = int(empresa_atual.ultimo_nsu or "0") if empresa_atual.ultimo_nsu else 0
                        docs_pendentes = max(0, max_nsu_c - ult_nsu_c)
                        max_ciclos = max(max_ciclos_base, (docs_pendentes // 50) + 10)  # +10 margem
                        lotes_vazios_consecutivos = 0  # Contador de lotes vazios
                        max_lotes_vazios = 10  # Para após 10 lotes vazios consecutivos
                        qtd_ciclo = 0
                        ultimo_cstat = None
                        
                        while ciclo < max_ciclos:
                            ciclo += 1
                            
                            # Garante NSU com 15 dígitos (preserva zeros à esquerda)
                            nsu_atual = str(empresa_atual.ultimo_nsu or '0').zfill(15)
                            nsu_atual_int = int(nsu_atual)
                            
                            # Verifica se já atingiu o maxNSU conhecido ANTES de consultar
                            max_nsu_conhecido_int = int(empresa_atual.max_nsu_conhecido) if empresa_atual.max_nsu_conhecido else None
                            
                            # VALIDAÇÃO: Nunca consultar com NSU maior que maxNSU conhecido
                            if max_nsu_conhecido_int and nsu_atual_int > max_nsu_conhecido_int:
                                logger.warning(
                                    f"NSU atual ({nsu_atual}) maior que maxNSU conhecido ({empresa_atual.max_nsu_conhecido}) | "
                                    f"Usando maxNSU conhecido apenas para consulta (sem reduzir ultimo_nsu) | "
                                    f"Empresa: {empresa_atual.razao_social}"
                                )
                                nsu_atual = empresa_atual.max_nsu_conhecido
                                nsu_atual_int = max_nsu_conhecido_int
                            
                            intervalo_minimo_periodica = 3600  # 60 minutos (alinhado à orientação SEFAZ "Tente após 1 hora" quando cStat 137)
                            esta_no_fim = bool(max_nsu_conhecido_int and nsu_atual_int >= max_nsu_conhecido_int)
                            
                            if esta_no_fim:
                                # Fim da fila - não há novos documentos disponíveis
                                # NUNCA consultar com ultNSU >= maxNSU, isso sempre causa erro 656
                                
                                # Verifica se é hora de verificar se há novos documentos (maxNSU mudou)
                                deve_verificar_max_nsu = False
                                
                                if empresa_atual.proxima_consulta_periodica:
                                    if datetime.now() >= empresa_atual.proxima_consulta_periodica:
                                        # Passou tempo suficiente, pode verificar se maxNSU mudou
                                        deve_verificar_max_nsu = True
                                        logger.info(f"Fim da fila - verificando se há novos documentos disponíveis | "
                                                  f"ultNSU: {nsu_atual} | maxNSU conhecido: {empresa_atual.max_nsu_conhecido} | "
                                                  f"Empresa: {empresa_atual.razao_social}")
                                    else:
                                        # Ainda não passou tempo suficiente
                                        tempo_restante = (empresa_atual.proxima_consulta_periodica - datetime.now()).total_seconds()
                                        logger.info(f"Fim da fila atingido - aguardando próxima verificação | "
                                                  f"ultNSU: {nsu_atual} | maxNSU: {empresa_atual.max_nsu_conhecido} | "
                                                  f"Próxima verificação em: {int(tempo_restante/60)} minutos | "
                                                  f"Empresa: {empresa_atual.razao_social}")
                                        break
                                elif empresa_atual.ultima_consulta:
                                    # Calcula se passou tempo suficiente desde última consulta
                                    tempo_desde_ultima = datetime.now() - empresa_atual.ultima_consulta
                                    if tempo_desde_ultima.total_seconds() >= intervalo_minimo_periodica:
                                        # Passou tempo suficiente, pode verificar
                                        deve_verificar_max_nsu = True
                                        logger.info(f"Fim da fila - verificando se há novos documentos disponíveis | "
                                                  f"ultNSU: {nsu_atual} | maxNSU conhecido: {empresa_atual.max_nsu_conhecido} | "
                                                  f"Tempo desde última: {int(tempo_desde_ultima.total_seconds()/60)} minutos | "
                                                  f"Empresa: {empresa_atual.razao_social}")
                                    else:
                                        # Agenda próxima verificação
                                        proxima_consulta = datetime.now() + timedelta(seconds=intervalo_minimo_periodica - tempo_desde_ultima.total_seconds())
                                        empresa_atual.proxima_consulta_periodica = proxima_consulta
                                        db_empresa.commit()
                                        tempo_restante = (proxima_consulta - datetime.now()).total_seconds()
                                        logger.info(f"Fim da fila atingido - aguardando intervalo mínimo | "
                                                  f"ultNSU: {nsu_atual} | maxNSU: {empresa_atual.max_nsu_conhecido} | "
                                                  f"Próxima verificação em: {int(tempo_restante/60)} minutos | "
                                                  f"Empresa: {empresa_atual.razao_social}")
                                        break
                                else:
                                    # Primeira vez, pode verificar
                                    deve_verificar_max_nsu = True
                                    logger.info(f"Fim da fila - primeira verificação de novos documentos | "
                                              f"ultNSU: {nsu_atual} | maxNSU conhecido: {empresa_atual.max_nsu_conhecido} | "
                                              f"Empresa: {empresa_atual.razao_social}")
                                
                                # Se deve verificar, faz consulta leve para atualizar maxNSU
                                if deve_verificar_max_nsu:
                                    max_nsu_novo, ult_nsu_novo, cstat, xml_retorno_consulta_leve = consultar_max_nsu(
                                        empresa_atual, pfx_bytes, cert_senha, ambiente, uf_autor, base_consulta
                                    )
                                    if base_consulta:
                                        base_consulta.ultima_consulta = datetime.now()
                                        db.commit()

                                    if cstat == '656':
                                        empresa_atual.bloqueio_656_ate = datetime.now() + timedelta(hours=2)
                                        empresa_atual.ultima_sync_mensagem = "Bloqueio 656 - aguardar 2h"
                                        empresa_atual.ultima_sync_cstat = "656"
                                        empresa_atual.ultima_sync_nfe_qtd = 0
                                        if base_consulta:
                                            base_consulta.bloqueio_656_ate = empresa_atual.bloqueio_656_ate
                                            db.commit()
                                        db_empresa.commit()
                                        logger.warning(f"Consulta leve retornou 656 | Bloqueio aplicado | Empresa: {empresa_atual.razao_social}")
                                        registrar_log_suporte(
                                            empresa_id=empresa_atual.id,
                                            nivel="WARNING",
                                            tipo="sefaz",
                                            mensagem="Consulta leve retornou 656 | Bloqueio aplicado",
                                            cstat=cstat,
                                            ult_nsu=nsu_atual,
                                            max_nsu=empresa_atual.max_nsu_conhecido
                                        )
                                        break

                                    # cStat 138: resposta contém docZips — processar e depois agendar 1h
                                    if cstat == '138' and xml_retorno_consulta_leve:
                                        try:
                                            novo_ult, max_nsu_proc, bloqueio_656, qtd_lote, cstat_lote = processar_retorno_sefaz(
                                                xml_retorno_consulta_leve,
                                                empresa_atual.id,
                                                empresa_atual.cnpj,
                                                pfx_bytes,
                                                cert_senha,
                                                ambiente
                                            )
                                            qtd_ciclo += int(qtd_lote or 0)
                                            if cstat_lote:
                                                ultimo_cstat = cstat_lote
                                            if bloqueio_656:
                                                logger.warning(f"Processamento da consulta leve interrompido por bloqueio 656 | Empresa: {empresa_atual.razao_social}")
                                                interromper_grupo = True
                                                break
                                            if novo_ult is not None:
                                                empresa_atual.ultimo_nsu = str(novo_ult).zfill(15)
                                            if max_nsu_proc is not None:
                                                empresa_atual.max_nsu_conhecido = str(max_nsu_proc).zfill(15)
                                            empresa_atual.ultima_consulta = datetime.now()
                                            empresa_atual.ultima_sync_mensagem = "Concluída"
                                            empresa_atual.proxima_consulta_periodica = datetime.now() + timedelta(seconds=intervalo_minimo_periodica)
                                            if base_consulta:
                                                base_consulta.proxima_consulta_periodica = empresa_atual.proxima_consulta_periodica
                                                base_consulta.ultima_consulta = empresa_atual.ultima_consulta
                                                db.commit()
                                            db_empresa.commit()
                                            logger.info(
                                                f"Documentos da consulta leve processados | ultNSU: {empresa_atual.ultimo_nsu} | "
                                                f"maxNSU: {empresa_atual.max_nsu_conhecido} | Próxima verificação em 1h | Empresa: {empresa_atual.razao_social}"
                                            )
                                            continue
                                        except Exception as e:
                                            logger.warning(f"Erro ao processar retorno da consulta leve: {e}")

                                    # Sem novos documentos (137) ou 138 sem XML: agenda próxima verificação em 1h e sai
                                    if cstat:
                                        ultimo_cstat = cstat
                                    empresa_atual.proxima_consulta_periodica = datetime.now() + timedelta(seconds=intervalo_minimo_periodica)
                                    empresa_atual.ultima_consulta = datetime.now()
                                    empresa_atual.ultima_sync_mensagem = "Concluída"
                                    if base_consulta:
                                        base_consulta.proxima_consulta_periodica = empresa_atual.proxima_consulta_periodica
                                        base_consulta.ultima_consulta = empresa_atual.ultima_consulta
                                        db.commit()
                                    db_empresa.commit()
                                    logger.info(f"Fim da fila atingido - aguardando intervalo mínimo (1h) | "
                                              f"ultNSU: {nsu_atual} | maxNSU: {empresa_atual.max_nsu_conhecido} | "
                                              f"Próxima verificação em: {int(intervalo_minimo_periodica/60)} min | "
                                              f"Empresa: {empresa_atual.razao_social}")
                                    break
                                else:
                                    # Não deve verificar ainda, sai do loop
                                    break
                        else:
                            # Se chegou aqui, significa que ultNSU < maxNSU (não está no fim da fila)
                            # Consulta normalmente (sem limite de segurança por diferença)
                            logger.info(f"Consulta #{ciclo} | Empresa: {empresa_atual.razao_social[:30]} | "
                                      f"NSU: {nsu_atual} | Ambiente: {'Produção' if ambiente == '1' else 'Homologação'}")
                            
                            # Limpa CNPJ (remove formatação: pontos, barras, hífens)
                            cnpj_limpo = "".join(filter(str.isdigit, str(empresa_atual.cnpj)))
                            
                            # Monta requisição XML
                            xml_request = (
                                '<?xml version="1.0" encoding="utf-8"?>'
                                '<soap12:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
                                'xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">'
                                '<soap12:Body><nfeDistDFeInteresse xmlns="http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe">'
                                '<nfeDadosMsg><distDFeInt xmlns="http://www.portalfiscal.inf.br/nfe" versao="1.01">'
                                f'<tpAmb>{ambiente}</tpAmb><cUFAutor>{uf_autor}</cUFAutor><CNPJ>{cnpj_limpo}</CNPJ>'
                                f'<distNSU><ultNSU>{nsu_atual}</ultNSU></distNSU>'
                                '</distDFeInt></nfeDadosMsg></nfeDistDFeInteresse></soap12:Body></soap12:Envelope>'
                            )
                            
                            # Faz requisição com retry
                            response, sucesso = fazer_requisicao_com_retry(
                                url_distribuicao,
                                xml_request.encode('utf-8'),
                                {'Content-Type': 'application/soap+xml; charset=utf-8'},
                                pfx_bytes,
                                cert_senha,
                                max_tentativas=3,
                                timeout=60
                            )
                            
                            if not sucesso or not response:
                                logger.error(f"Falha na comunicação após retries | Empresa: {empresa_atual.razao_social}")
                                break
                            
                            # Processa retorno
                            novo_ult, max_nsu, bloqueio_656, qtd_lote, cstat_lote = processar_retorno_sefaz(
                                response.text, empresa_atual.id, empresa_atual.cnpj, pfx_bytes, cert_senha, ambiente
                            )
                            qtd_ciclo += int(qtd_lote or 0)
                            if cstat_lote:
                                ultimo_cstat = cstat_lote
                            
                            # Se bloqueado por 656, para imediatamente
                            if bloqueio_656:
                                logger.warning(f"Processamento interrompido por bloqueio 656 | Empresa: {empresa_atual.razao_social}")
                                interromper_grupo = True
                                break
                            
                            # Se erro crítico, tenta recuperação automática antes de parar
                            if novo_ult is None:
                                # Verifica se é possível tentar recuperação automática
                                # Conta quantas vezes já tentou recuperar nesta sessão
                                if not hasattr(empresa_atual, '_tentativas_recuperacao'):
                                    empresa_atual._tentativas_recuperacao = 0
                                
                                # Tenta recuperação automática apenas uma vez por sessão
                                if empresa_atual._tentativas_recuperacao == 0:
                                    logger.warning(f"Erro crítico detectado - tentando recuperação automática | Empresa: {empresa_atual.razao_social}")
                                    empresa_atual._tentativas_recuperacao = 1
                                    
                                    # Recarrega empresa do banco para ter dados atualizados
                                    db_empresa.refresh(empresa_atual)
                                    
                                    # Tenta recuperar NSU automaticamente
                                    sucesso_recuperacao, nsu_recuperado, mensagem = recuperar_nsu_automaticamente(
                                        empresa_atual, pfx_bytes, cert_senha, ambiente, uf_autor,
                                        motivo="erro_critico_na_consulta"
                                    )
                                    
                                    if sucesso_recuperacao and nsu_recuperado:
                                        # SEMPRE usar o NSU retornado pela SEFAZ na próxima solicitação
                                        nsu_recuperado_val = nsu_recuperado.zfill(15)
                                        ultimo_nsu_atual = int(empresa_atual.ultimo_nsu or '0')
                                        empresa_atual.ultimo_nsu = nsu_recuperado_val
                                        if int(nsu_recuperado_val) < ultimo_nsu_atual:
                                            logger.info(
                                                f"NSU recuperado menor que o armazenado - usando o da SEFAZ | "
                                                f"Armazenado: {empresa_atual.ultimo_nsu} | Recuperado: {nsu_recuperado_val} | "
                                                f"Empresa: {empresa_atual.razao_social}"
                                            )
                                        empresa_atual.ultima_consulta = datetime.now()
                                        empresa_atual.ultima_sync_mensagem = "Concluída"
                                        empresa_atual.bloqueio_656_ate = None
                                        db_empresa.commit()
                                        logger.info(f"✓ NSU recuperado automaticamente com sucesso | Novo NSU: {nsu_recuperado} | Empresa: {empresa_atual.razao_social}")
                                        # Continua processamento com NSU recuperado
                                        continue
                                    else:
                                        logger.warning(f"Recuperação automática não foi possível | {mensagem} | Empresa: {empresa_atual.razao_social}")
                                        logger.warning(f"Processamento interrompido por erro | Empresa: {empresa_atual.razao_social}")
                                        break
                                else:
                                    logger.warning(f"Recuperação automática já tentada nesta sessão | Processamento interrompido | Empresa: {empresa_atual.razao_social}")
                                    break
                            
                            # Verifica se lote estava vazio (sem progresso no NSU)
                            novo_ult_int = int(novo_ult)
                            max_nsu_int = int(max_nsu) if max_nsu else None
                            
                            if novo_ult_int == nsu_atual_int:
                                lotes_vazios_consecutivos += 1
                                # CORREÇÃO: Diferencia fim da fila de erro temporário
                                if max_nsu_int and novo_ult_int >= max_nsu_int:
                                    # Fim da fila confirmado - não é erro
                                    logger.info(f"Fim da fila confirmado (lote vazio) | ultNSU: {novo_ult} >= maxNSU: {max_nsu} | "
                                              f"Empresa: {empresa_atual.razao_social}")
                                    # Agenda próxima consulta periódica
                                    empresa_atual.proxima_consulta_periodica = datetime.now() + timedelta(seconds=1800)
                                    if base_consulta:
                                        base_consulta.proxima_consulta_periodica = empresa_atual.proxima_consulta_periodica
                                        base_consulta.ultima_consulta = datetime.now()
                                        db.commit()
                                    db_empresa.commit()
                                    break
                                else:
                                    # Lote vazio mas não está no fim da fila - pode ser erro temporário
                                    logger.info(f"Lote vazio (erro temporário?) | NSU não mudou: {novo_ult} | maxNSU: {max_nsu or 'N/A'} | "
                                              f"Lotes vazios consecutivos: {lotes_vazios_consecutivos}/{max_lotes_vazios} | "
                                              f"Empresa: {empresa_atual.razao_social}")
                            else:
                                lotes_vazios_consecutivos = 0  # Reset contador se houve progresso
                            
                            # SEMPRE usar o ultNSU retornado pela SEFAZ na próxima solicitação
                            # (evita erro 656: "Deve ser utilizado o ultNSU nas solicitacoes subsequentes")
                            novo_ult_val = novo_ult.zfill(15) if isinstance(novo_ult, str) else str(novo_ult).zfill(15)
                            ultimo_nsu_antigo = empresa_atual.ultimo_nsu
                            empresa_atual.ultimo_nsu = novo_ult_val
                            if int(novo_ult_val) < int(ultimo_nsu_antigo or '0'):
                                logger.info(
                                    f"ultNSU da SEFAZ (consulta) menor que o armazenado - usando o da SEFAZ | "
                                    f"Armazenado: {ultimo_nsu_antigo} | Retornado: {novo_ult_val} | "
                                    f"Empresa: {empresa_atual.razao_social}"
                                )
                            # CORREÇÃO 3: Atualiza max_nsu_conhecido apenas se o novo maxNSU for maior
                            if max_nsu:
                                try:
                                    max_nsu_int = int(max_nsu)
                                    max_nsu_conhecido_int = int(empresa_atual.max_nsu_conhecido) if empresa_atual.max_nsu_conhecido and empresa_atual.max_nsu_conhecido != '000000000000000' else 0
                                    if max_nsu_int > max_nsu_conhecido_int:
                                        empresa_atual.max_nsu_conhecido = max_nsu.zfill(15)
                                        logger.info(f"max_nsu_conhecido atualizado durante sincronizacao | Antes: {empresa_atual.max_nsu_conhecido or 'N/A'} | Depois: {max_nsu}")
                                    else:
                                        logger.debug(f"maxNSU ({max_nsu}) não é maior que max_nsu_conhecido atual ({empresa_atual.max_nsu_conhecido}), mantendo valor atual")
                                except Exception as e:
                                    logger.warning(f"Erro ao atualizar max_nsu_conhecido durante sincronizacao: {e}")
                                    # Se der erro, ainda assim atualiza (comportamento antigo como fallback)
                                    empresa_atual.max_nsu_conhecido = max_nsu.zfill(15)
                            empresa_atual.ultima_consulta = datetime.now()
                            # ultima_sync_mensagem será definida ao sair do loop ("Sincronização concluída")
                            db_empresa.commit()

                            # Comparação numérica para verificar fim da fila
                            max_nsu_int = int(max_nsu) if max_nsu else None
                            
                            # Verifica se atingiu o maxNSU retornado
                            if max_nsu_int and novo_ult_int >= max_nsu_int:
                                logger.info(f"Fim da fila atingido | ultNSU: {novo_ult} | maxNSU: {max_nsu} | "
                                          f"Empresa: {empresa_atual.razao_social}")
                                # Agenda próxima consulta periódica (30 minutos)
                                empresa_atual.proxima_consulta_periodica = datetime.now() + timedelta(seconds=1800)
                                if base_consulta:
                                    base_consulta.proxima_consulta_periodica = empresa_atual.proxima_consulta_periodica
                                    base_consulta.ultima_consulta = datetime.now()
                                    db.commit()
                                db_empresa.commit()
                                break
                            
                            # Para se recebeu múltiplos lotes vazios consecutivos (apenas se não estiver no fim da fila)
                            if lotes_vazios_consecutivos >= max_lotes_vazios:
                                # Se não está no fim da fila, pode ser erro temporário - aguarda mais
                                if not max_nsu_int or novo_ult_int < max_nsu_int:
                                    logger.warning(f"Muitos lotes vazios consecutivos ({lotes_vazios_consecutivos}) mas não está no fim da fila | "
                                                 f"ultNSU: {novo_ult} | maxNSU: {max_nsu or 'N/A'} | "
                                                 f"Pode ser erro temporário - aguardando antes de continuar | "
                                                 f"Empresa: {empresa_atual.razao_social}")
                                    # Aguarda mais tempo antes de continuar (backoff)
                                    time.sleep(30)  # Aguarda 30 segundos
                                    # Reduz contador para tentar novamente
                                    lotes_vazios_consecutivos = max_lotes_vazios - 2
                                else:
                                    logger.info(f"Parando após {max_lotes_vazios} lotes vazios consecutivos no fim da fila | ultNSU: {novo_ult} | "
                                              f"maxNSU: {max_nsu or 'N/A'} | Empresa: {empresa_atual.razao_social}")
                                    break

                        if interromper_grupo:
                            break
                        # Pausa entre lotes para evitar sobrecarga e rate limit na Receita (5-10 s)
                        intervalo_entre_consultas = 5
                        if lotes_vazios_consecutivos > 0:
                            intervalo_entre_consultas = min(10, 5 * (2 ** min(lotes_vazios_consecutivos - 1, 2)))
                        time.sleep(intervalo_entre_consultas)
                
                        if ciclo >= max_ciclos and empresa_atual.max_nsu_conhecido:
                            ult_i = int(empresa_atual.ultimo_nsu or "0")
                            max_i = int(empresa_atual.max_nsu_conhecido)
                            if ult_i < max_i:
                                logger.info(
                                    f"Sincronização parou no limite de ciclos ({max_ciclos}) | "
                                    f"Ainda há documentos pendentes (ultNSU: {empresa_atual.ultimo_nsu} < maxNSU: {empresa_atual.max_nsu_conhecido}) | "
                                    f"Próxima execução continuará de onde parou | Empresa: {empresa_atual.razao_social}"
                                )
                        logger.info("=" * 80)
                        if ult_inicio is not None:
                            ult_fim = (empresa_atual.ultimo_nsu or "000000000000000").zfill(15)
                            max_fim = (empresa_atual.max_nsu_conhecido or "000000000000000").zfill(15)
                            nsu_avancou = int(ult_fim) > int(ult_inicio)
                            salto = max(0, int(ult_fim) - int(ult_inicio))
                            possivel_pulo = salto > 1 and nsu_avancou
                            if nsu_avancou:
                                resumo = (
                                    f"Sincronização começou com ultNSU: {ult_inicio} | maxNSU: {max_inicio} e terminou com ultNSU: {ult_fim} | maxNSU: {max_fim}, {ciclo} ciclos, nsu avançou"
                                )
                            else:
                                if int(ult_fim) < int(ult_inicio):
                                    resumo = (
                                        f"Sincronização começou com ultNSU: {ult_inicio} | maxNSU: {max_inicio} e terminou com ultNSU: {ult_fim} | maxNSU: {max_fim}, {ciclo} ciclos. "
                                        f"NSU retrocedeu (consulta leve; uso do ultNSU da SEFAZ). Próxima execução continuará de {ult_fim} até {max_fim}."
                                    )
                                else:
                                    resumo = (
                                        f"Sincronização começou com ultNSU: {ult_inicio} | maxNSU: {max_inicio} e terminou com ultNSU: {ult_fim} | maxNSU: {max_fim}, {ciclo} ciclos, nsu não avançou"
                                    )
                            logger.info(resumo)
                            eventos_faltantes = max(0, int(max_fim) - int(ult_fim))
                            logger.info("Faltam %d eventos para o fim da fila.", eventos_faltantes)
                            registrar_log_suporte(
                                empresa_id=empresa_atual.id,
                                nivel="WARNING" if possivel_pulo else "INFO",
                                tipo="nsu",
                                mensagem=(
                                    f"Ciclo NSU inicio={ult_inicio} fim={ult_fim} | "
                                    f"max inicio={max_inicio} fim={max_fim} | "
                                    f"{ciclo} lote(s)"
                                    + (
                                        f" | possivel pulo (fim > inicio+1, delta={salto})"
                                        if possivel_pulo else ""
                                    )
                                ),
                                ult_nsu=ult_fim,
                                max_nsu=max_fim,
                                contexto={
                                    "ciclo_id": ciclo_id,
                                    "nsu_inicio": ult_inicio,
                                    "nsu_fim": ult_fim,
                                    "max_inicio": max_inicio,
                                    "max_fim": max_fim,
                                    "lotes": ciclo,
                                    "possivel_pulo": possivel_pulo,
                                    "delta_nsu": salto,
                                },
                            )
                            registrar_timeline_evento(
                                empresa_id=empresa_atual.id,
                                cnpj_base=cnpj_base,
                                ambiente=ambiente_base,
                                ciclo_id=ciclo_id,
                                status="nsu_ciclo",
                                mensagem=f"NSU {ult_inicio} -> {ult_fim}",
                                extra={
                                    "nsu_inicio": ult_inicio,
                                    "nsu_fim": ult_fim,
                                    "max_inicio": max_inicio,
                                    "max_fim": max_fim,
                                    "possivel_pulo": possivel_pulo,
                                },
                            )
                        if ultimo_cstat is not None or qtd_ciclo:
                            empresa_atual.ultima_sync_nfe_qtd = qtd_ciclo
                            if ultimo_cstat:
                                empresa_atual.ultima_sync_cstat = str(ultimo_cstat)[:10]
                        empresa_atual.ultima_sync_mensagem = f"Concluída ({ciclo} ciclos)"
                        db_empresa.commit()
                        logger.info(f"Sincronização concluída | Empresa: {empresa_atual.razao_social} | "
                                  f"Ciclos: {ciclo} | Último NSU: {empresa_atual.ultimo_nsu}")
                        logger.info("=" * 80)
                        logger.info("FIM DO CICLO DA EMPRESA")
                        logger.info("=" * 80)
                        registrar_timeline_evento(
                            empresa_id=empresa_atual.id,
                            cnpj_base=cnpj_base,
                            ambiente=ambiente_base,
                            ciclo_id=ciclo_id,
                            status="empresa_fim",
                            mensagem="Fim do ciclo da empresa"
                        )
                
                    except Exception as e:
                        logger.error(f"Erro ao processar empresa | ID: {empresa.id} | Erro: {e}\n{traceback.format_exc()}")
                        registrar_timeline_evento(
                            empresa_id=empresa.id,
                            cnpj_base=cnpj_base,
                            ambiente=ambiente_base,
                            ciclo_id=ciclo_id,
                            status="empresa_erro",
                            mensagem="Erro no ciclo da empresa",
                            extra={"erro": str(e)}
                        )
                    finally:
                        clear_empresa_log_context()
                        db_empresa.close()
        
            except Exception as e:
                logger.error(f"Erro crítico na sincronização | Erro: {e}\n{traceback.format_exc()}")
                registrar_timeline_evento(
                    empresa_id=grupo[0].id,
                    cnpj_base=cnpj_base,
                    ambiente=ambiente_base,
                    ciclo_id=ciclo_id,
                    status="base_erro",
                    mensagem="Erro no ciclo da base",
                    extra={"erro": str(e)}
                )
            finally:
                if base_consulta:
                    base_consulta.processamento_ate = None
                    base_consulta.ultima_consulta = datetime.now()
                    db.commit()
                registrar_timeline_evento(
                    empresa_id=grupo[0].id,
                    cnpj_base=cnpj_base,
                    ambiente=ambiente_base,
                    ciclo_id=ciclo_id,
                    status="base_fim",
                    mensagem="Fim do ciclo da base"
                )
    finally:
        db.close()
    
    logger.info("=" * 80)
    logger.info("FIM DO CICLO DE SINCRONIZAÇÃO")
    logger.info("=" * 80)

if __name__ == "__main__":
    executar_consulta()
