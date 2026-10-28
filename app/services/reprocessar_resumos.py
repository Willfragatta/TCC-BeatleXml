"""
Serviço para reprocessar resumos pendentes e buscar XMLs completos.
Este serviço deve ser executado periodicamente (ex: a cada 10 minutos).
"""
import time
import base64
import gzip
import re
import os
import certifi
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from requests_pkcs12 import post
from requests.exceptions import SSLError
from app.models.database import SessionLocal, Empresa, NotaFiscal, ResumoPendente, BaseConsulta
from app.core.security import descriptografar
from app.core.logger import setup_logger
from app.services.parser import extrair_dados_xml
from app.services.assinatura_evento import assinar_xml_evento
# URLs e funções compartilhadas
URLS_DISTRIBUICAO = {
    '1': "https://www1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx",
    '2': "https://hom1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx"
}

URLS_EVENTO = {
    '1': "https://www1.nfe.fazenda.gov.br/NFeRecepcaoEvento4/NFeRecepcaoEvento4.asmx",
    '2': "https://hom1.nfe.fazenda.gov.br/NFeRecepcaoEvento4/NFeRecepcaoEvento4.asmx"
}

def extrair_cnpj_base(cnpj):
    cnpj_limpo = "".join(filter(str.isdigit, str(cnpj)))
    return cnpj_limpo[:8] if len(cnpj_limpo) >= 8 else None

def obter_base_consulta(db, cnpj_base, ambiente):
    if not cnpj_base:
        return None
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

def enviar_ciencia_operacao(chave, cnpj, pfx_bytes, senha, ambiente='1'):
    """
    Envia o evento de Ciência da Operação (210210) para liberar o XML completo.
    Retorna (sucesso: bool, ja_processado: bool).
    - (True, False): ciência enviada agora (cStat=135).
    - (True, True): lote já processado (cStat=128) — ciência já tinha sido enviada.
    - (False, False): falha ao enviar.
    """
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
        return False, False

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
            if cstat == '135':
                logger.info(f"Ciência da Operação enviada | Chave: {chave[:10]}... | CNPJ: {cnpj_limpo[:8]}...")
                return True, False
            if cstat == '128':
                logger.info(f"Ciência já processada (cStat=128) | Chave: {chave[:10]}... | Tentando buscar XML.")
                return True, True
            logger.warning(
                f"Ciência retornou cStat={cstat} | Motivo: {xmotivo or 'N/A'} | Chave: {chave[:10]}..."
            )
            return False, False
        else:
            cstat, xmotivo = extrair_status_retorno(response.text)
            detalhe = f" | cStat={cstat} | Motivo={xmotivo}" if cstat or xmotivo else ""
            logger.warning(
                f"Falha ao enviar ciência | Status: {response.status_code} | Chave: {chave[:10]}...{detalhe}"
            )
            logger.debug(f"Resposta ciencia (status {response.status_code}): {response.text[:800]}")
            return False, False
    except SSLError as e:
        logger.error(
            f"Erro TLS ao enviar ciência | Chave: {chave[:10]}... | Erro: {e} | "
            f"CA: {obter_verify_tls()}"
        )
        return False, False
    except Exception as e:
        logger.error(f"Erro ao enviar ciência da operação | Chave: {chave[:10]}... | Erro: {e}")
        return False, False

def fazer_requisicao_com_retry(url, data, headers, pkcs12_data, pkcs12_password, max_tentativas=3, timeout=60):
    """
    Faz requisição com retry e backoff exponencial.
    Retorna (response, sucesso)
    """
    from requests.exceptions import RequestException, Timeout, ConnectionError
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
            
            logger.warning(f"Requisição retornou status {response.status_code} | Tentativa: {tentativa}/{max_tentativas}")
            
            if response.status_code == 404:
                logger.error(f"Erro 404 - URL não encontrada | URL: {url}")
                return None, False
            
        except SSLError as e:
            logger.error(f"Erro TLS na requisicao | Erro: {e} | CA: {obter_verify_tls()}")
            return None, False
        except Timeout:
            logger.warning(f"Timeout na requisição | Tentativa: {tentativa}/{max_tentativas}")
            if tentativa < max_tentativas:
                time.sleep(2 ** tentativa)
            else:
                return None, False
                
        except ConnectionError:
            logger.warning(f"Erro de conexão | Tentativa: {tentativa}/{max_tentativas}")
            if tentativa < max_tentativas:
                time.sleep(2 ** tentativa)
            else:
                return None, False
                
        except RequestException as e:
            logger.error(f"Erro na requisição | Tentativa: {tentativa}/{max_tentativas} | Erro: {e}")
            if tentativa < max_tentativas:
                time.sleep(2 ** tentativa)
            else:
                return None, False
                
        except Exception as e:
            logger.error(f"Erro inesperado na requisição | Tentativa: {tentativa}/{max_tentativas} | Erro: {e}")
            return None, False
    
    return None, False

logger = setup_logger()

def buscar_xml_completo_por_chave(chave, cnpj, pfx_bytes, cert_senha, ambiente, uf_autor):
    """
    Busca XML completo na SEFAZ usando a chave de acesso.
    Retorna (xml_completo, sucesso)
    """
    url_distribuicao = URLS_DISTRIBUICAO.get(ambiente, URLS_DISTRIBUICAO['1'])
    cnpj_limpo = "".join(filter(str.isdigit, cnpj))
    
    # Monta requisição para buscar por chave de acesso
    xml_request = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap12:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">'
        '<soap12:Body><nfeDistDFeInteresse xmlns="http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe">'
        '<nfeDadosMsg><distDFeInt xmlns="http://www.portalfiscal.inf.br/nfe" versao="1.01">'
        f'<tpAmb>{ambiente}</tpAmb><cUFAutor>{uf_autor}</cUFAutor><CNPJ>{cnpj_limpo}</CNPJ>'
        f'<consChNFe><chNFe>{chave}</chNFe></consChNFe>'
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
            return None, False
        
        # Extrai XML do retorno
        xml_retorno = response.text
        
        # Procura por docZip com o XML completo
        try:
            root = ET.fromstring(xml_retorno)
            body = root.find('.//{http://www.w3.org/2003/05/soap-envelope}Body')
            if body is not None:
                ret_dist = body.find('.//{http://www.portalfiscal.inf.br/nfe}retDistDFeInt')
                if ret_dist is not None:
                    # Procura por docZip
                    doczip = ret_dist.find('.//{http://www.portalfiscal.inf.br/nfe}docZip')
                    if doczip is not None and doczip.text:
                        # Descompacta o XML
                        xml_descompactado = gzip.decompress(base64.b64decode(doczip.text)).decode('utf-8')
                        # Verifica se é XML completo (não resumo)
                        if "<nfeProc" in xml_descompactado or "<procNFe" in xml_descompactado:
                            return xml_descompactado, True
        except Exception as e:
            logger.warning(f"Erro ao extrair XML completo do retorno: {e}")
        
        # Fallback: tenta regex
        doczip_match = re.search(r'<docZip[^>]*>(.*?)</docZip>', xml_retorno, re.DOTALL)
        if doczip_match:
            conteudo_base64 = doczip_match.group(1)
            xml_descompactado = gzip.decompress(base64.b64decode(conteudo_base64)).decode('utf-8')
            if "<nfeProc" in xml_descompactado or "<procNFe" in xml_descompactado:
                return xml_descompactado, True
        
        return None, False
        
    except Exception as e:
        logger.error(f"Erro ao buscar XML completo por chave | Chave: {chave[:10]}... | Erro: {e}")
        return None, False


def buscar_completa_para_nota_id(db, nota_id, empresa_id):
    """
    Tenta trazer o XML completo para uma nota que está como RESUMO ou CANCELADA (por nota_id).
    Retorna (ok: bool, erro: str | None). Se ok=True, erro é None.
    """
    nota = db.query(NotaFiscal).filter(
        NotaFiscal.id == nota_id,
        NotaFiscal.empresa_id == empresa_id,
    ).first()
    if not nota:
        return False, "Nota não encontrada"
    if nota.status_manifesto not in ("RESUMO", "CANCELADA"):
        return False, "Nota não é do tipo resumo ou cancelada sem XML completo"
    chave = nota.chave_acesso
    if not chave or len(chave) != 44:
        return False, "Chave de acesso inválida"

    empresa = db.query(Empresa).filter_by(id=empresa_id).first()
    if not empresa or empresa.status != "aprovado":
        return False, "Empresa não encontrada ou não aprovada"

    resumo = db.query(ResumoPendente).filter_by(
        empresa_id=empresa_id,
        chave_acesso=chave,
    ).first()
    if not resumo:
        resumo = ResumoPendente(
            empresa_id=empresa_id,
            chave_acesso=chave,
            data_proxima_tentativa=datetime.now(),
            status="pendente",
        )
        db.add(resumo)
        db.commit()

    try:
        cert_base64_descripto = descriptografar(empresa.certificado_base64)
        pfx_bytes = base64.b64decode(cert_base64_descripto)
        cert_senha = descriptografar(empresa.certificado_senha)
    except Exception as e:
        return False, f"Erro ao usar certificado: {e}"

    ambiente = empresa.ambiente or "1"
    uf_autor = empresa.uf_autor or "41"

    if not resumo.data_ciencia_enviada:
        sucesso_ciencia, ja_processado = enviar_ciencia_operacao(
            chave, empresa.cnpj, pfx_bytes, cert_senha, ambiente
        )
        if not sucesso_ciencia:
            return False, "Falha ao enviar ciência da operação."
        if ja_processado:
            resumo.data_ciencia_enviada = datetime.now() - timedelta(minutes=6)
            resumo.data_proxima_tentativa = datetime.now()
            resumo.status = "pendente"
            resumo.updated_at = datetime.now()
            db.commit()
        else:
            resumo.data_ciencia_enviada = datetime.now()
            resumo.data_proxima_tentativa = datetime.now() + timedelta(minutes=5)
            resumo.status = "pendente"
            resumo.updated_at = datetime.now()
            db.commit()
            return False, "Ciência enviada. Aguarde alguns minutos e tente novamente."

    tempo_desde_ciencia = (datetime.now() - resumo.data_ciencia_enviada).total_seconds()
    if tempo_desde_ciencia < 300:
        return False, "Aguarde alguns minutos após o envio da ciência e tente novamente."

    xml_completo, sucesso = buscar_xml_completo_por_chave(
        chave, empresa.cnpj, pfx_bytes, cert_senha, ambiente, uf_autor
    )
    resumo.tentativas += 1

    if sucesso and xml_completo:
        nota.xml_conteudo = xml_completo
        nota.status_manifesto = "COMPLETA"
        info = extrair_dados_xml(xml_completo)
        if info.get("numero"):
            nota.numero_nfe = info.get("numero")
        if info.get("serie") is not None:
            nota.serie = info.get("serie")
        resumo.status = "completo"
        resumo.updated_at = datetime.now()
        try:
            db.commit()
            return True, None
        except Exception as e:
            db.rollback()
            return False, f"Erro ao salvar: {e}"

    return False, "XML completo ainda não disponível. Tente novamente mais tarde."


def processar_resumos_pendentes():
    """
    Processa resumos pendentes que precisam de ciência da operação e busca XMLs completos.
    Deve ser executado periodicamente (ex: a cada 10 minutos).
    """
    logger.info("=" * 80)
    logger.info("INÍCIO DO PROCESSAMENTO DE RESUMOS PENDENTES")
    logger.info("=" * 80)
    
    db = SessionLocal()
    try:
        # Recupera resumos "processando" travados (ex: queda do worker)
        limite_travado_min = int(os.getenv("RESUMOS_PROCESSANDO_TIMEOUT_MIN", "30"))
        limite_travado = datetime.now() - timedelta(minutes=limite_travado_min)
        travados = db.query(ResumoPendente).filter(
            ResumoPendente.status == 'processando',
            ResumoPendente.updated_at <= limite_travado
        ).all()
        if travados:
            logger.warning(f"Resumos 'processando' travados: {len(travados)} | Resetando para pendente")
            for resumo_travado in travados:
                resumo_travado.status = 'pendente'
                resumo_travado.data_proxima_tentativa = datetime.now()
                resumo_travado.updated_at = datetime.now()
            db.commit()

        # Busca resumos pendentes que estão prontos para processamento
        agora = datetime.now()
        resumos_pendentes = db.query(ResumoPendente).filter(
            ResumoPendente.status == 'pendente',
            ResumoPendente.data_proxima_tentativa <= agora
        ).limit(50).all()  # Processa no máximo 50 por vez
        
        if not resumos_pendentes:
            logger.info("Nenhum resumo pendente para processar")
            return
        
        logger.info(f"Processando {len(resumos_pendentes)} resumos pendentes")
        
        processados = 0
        completos = 0
        erros = 0
        
        max_tentativas = int(os.getenv("RESUMOS_MAX_TENTATIVAS", "10"))
        max_idade_dias = int(os.getenv("RESUMOS_MAX_IDADE_DIAS", "30"))
        limite_idade = agora - timedelta(days=max_idade_dias)

        for resumo in resumos_pendentes:
            try:
                # Busca empresa
                empresa = db.query(Empresa).filter_by(id=resumo.empresa_id).first()
                if not empresa or empresa.status != 'aprovado':
                    logger.warning(f"Empresa não encontrada ou não aprovada | Resumo ID: {resumo.id}")
                    resumo.status = 'erro'
                    resumo.updated_at = datetime.now()
                    db.commit()
                    continue

                # Resumos muito antigos ou com muitas tentativas entram em erro
                if resumo.created_at and resumo.created_at < limite_idade:
                    logger.warning(
                        f"Resumo muito antigo | Chave: {resumo.chave_acesso[:10]}... | "
                        f"Criado: {resumo.created_at} | Marcando como erro"
                    )
                    resumo.status = 'erro'
                    resumo.updated_at = datetime.now()
                    db.commit()
                    erros += 1
                    continue

                if resumo.tentativas >= max_tentativas:
                    logger.warning(
                        f"Resumo excedeu tentativas | Chave: {resumo.chave_acesso[:10]}... | "
                        f"Tentativas: {resumo.tentativas} | Marcando como erro"
                    )
                    resumo.status = 'erro'
                    resumo.updated_at = datetime.now()
                    db.commit()
                    erros += 1
                    continue

                # Se a nota já está COMPLETA, marca o resumo e segue
                nota_existente = db.query(NotaFiscal).filter_by(chave_acesso=resumo.chave_acesso).first()
                if nota_existente and nota_existente.status_manifesto == "COMPLETA" and nota_existente.xml_conteudo:
                    resumo.status = 'completo'
                    resumo.updated_at = datetime.now()
                    try:
                        db.commit()
                        completos += 1
                        logger.info(
                            f"Resumo já resolvido (nota completa) | Chave: {resumo.chave_acesso[:10]}... | "
                            f"Empresa: {empresa.razao_social}"
                        )
                    except Exception as e:
                        db.rollback()
                        logger.error(
                            f"Erro ao marcar resumo como completo (nota já completa) | "
                            f"Chave: {resumo.chave_acesso[:10]}... | Erro: {e}"
                        )
                        erros += 1
                    continue
                
                # PROTEÇÃO: Verifica bloqueio por base (CNPJ base) antes de processar resumo
                cnpj_base = extrair_cnpj_base(empresa.cnpj)
                base_consulta = obter_base_consulta(db, cnpj_base, empresa.ambiente or '1')
                if base_bloqueada(base_consulta):
                    tempo_restante = base_consulta.bloqueio_656_ate - datetime.now()
                    minutos = int(tempo_restante.total_seconds() / 60)
                    logger.info(f"CNPJ base bloqueado por 656 - pulando resumos | Base: {cnpj_base} | "
                              f"Aguarde: {minutos} minutos | Resumo ID: {resumo.id}")
                    resumo.status = 'pendente'
                    resumo.data_proxima_tentativa = base_consulta.bloqueio_656_ate + timedelta(minutes=5)
                    resumo.updated_at = datetime.now()
                    db.commit()
                    continue

                # PROTEÇÃO: Verifica bloqueio 656 antes de processar resumo
                if empresa.bloqueio_656_ate and datetime.now() < empresa.bloqueio_656_ate:
                    tempo_restante = empresa.bloqueio_656_ate - datetime.now()
                    minutos = int(tempo_restante.total_seconds() / 60)
                    logger.info(f"Empresa bloqueada por 656 - pulando resumos | Empresa: {empresa.razao_social} | "
                              f"Aguarde: {minutos} minutos | Resumo ID: {resumo.id}")
                    # Mantém como pendente e agenda para depois do bloqueio
                    resumo.status = 'pendente'
                    resumo.data_proxima_tentativa = empresa.bloqueio_656_ate + timedelta(minutes=5)
                    resumo.updated_at = datetime.now()
                    db.commit()
                    continue
                
                # PROTEÇÃO: Verifica intervalo mínimo desde última consulta da empresa
                if empresa.ultima_consulta:
                    tempo_desde_ultima = datetime.now() - empresa.ultima_consulta
                    intervalo_minimo = timedelta(minutes=5)  # Mínimo de 5 minutos entre consultas da mesma empresa
                    if tempo_desde_ultima < intervalo_minimo:
                        tempo_restante = intervalo_minimo - tempo_desde_ultima
                        logger.info(f"Aguardando intervalo mínimo entre consultas | Empresa: {empresa.razao_social} | "
                                  f"Aguarde: {int(tempo_restante.total_seconds()/60)} minutos | Resumo ID: {resumo.id}")
                        resumo.data_proxima_tentativa = datetime.now() + tempo_restante
                        resumo.status = 'pendente'
                        resumo.updated_at = datetime.now()
                        db.commit()
                        continue
                
                # Descriptografa certificado
                try:
                    cert_base64_descripto = descriptografar(empresa.certificado_base64)
                    pfx_bytes = base64.b64decode(cert_base64_descripto)
                    cert_senha = descriptografar(empresa.certificado_senha)
                except Exception as e:
                    logger.error(f"Erro ao descriptografar certificado | Empresa: {empresa.razao_social} | Erro: {e}")
                    resumo.status = 'erro'
                    resumo.updated_at = datetime.now()
                    db.commit()
                    continue
                
                ambiente = empresa.ambiente or '1'
                uf_autor = empresa.uf_autor or '41'
                
                # Marca como processando
                resumo.status = 'processando'
                resumo.updated_at = datetime.now()
                db.commit()
                
                # Se ainda não enviou ciência, envia agora
                if not resumo.data_ciencia_enviada:
                    logger.info(f"Enviando ciência da operação | Chave: {resumo.chave_acesso[:10]}... | Empresa: {empresa.razao_social}")
                    sucesso_ciencia, ja_processado = enviar_ciencia_operacao(
                        resumo.chave_acesso, empresa.cnpj, pfx_bytes, cert_senha, ambiente
                    )
                    if not sucesso_ciencia:
                        resumo.data_proxima_tentativa = datetime.now() + timedelta(minutes=10)
                        resumo.status = 'pendente'
                        resumo.updated_at = datetime.now()
                        db.commit()
                        logger.warning(f"Falha ao enviar ciência | Chave: {resumo.chave_acesso[:10]}... | Tentará novamente em 10 minutos")
                        erros += 1
                        continue
                    if ja_processado:
                        resumo.data_ciencia_enviada = datetime.now() - timedelta(minutes=6)
                        resumo.data_proxima_tentativa = datetime.now()
                        resumo.status = 'pendente'
                        resumo.updated_at = datetime.now()
                        db.commit()
                        logger.info(f"Ciência já processada (cStat=128) | Chave: {resumo.chave_acesso[:10]}... | Tentando buscar XML")
                    else:
                        resumo.data_ciencia_enviada = datetime.now()
                        resumo.data_proxima_tentativa = datetime.now() + timedelta(minutes=5)
                        resumo.status = 'pendente'
                        resumo.updated_at = datetime.now()
                        db.commit()
                        logger.info(f"Ciência enviada com sucesso | Chave: {resumo.chave_acesso[:10]}... | Próxima tentativa em 5 minutos")
                        processados += 1
                        time.sleep(2)
                        continue
                
                # Se já enviou ciência, tenta buscar XML completo
                tempo_desde_ciencia = (agora - resumo.data_ciencia_enviada).total_seconds()
                if tempo_desde_ciencia < 300:  # Menos de 5 minutos
                    # Ainda não passou tempo suficiente, agenda para depois
                    resumo.data_proxima_tentativa = resumo.data_ciencia_enviada + timedelta(minutes=5)
                    resumo.status = 'pendente'
                    resumo.updated_at = datetime.now()
                    db.commit()
                    logger.info(f"Aguardando processamento da ciência | Chave: {resumo.chave_acesso[:10]}... | "
                              f"Tempo desde ciência: {int(tempo_desde_ciencia/60)} minutos")
                    continue
                
                # Tenta buscar XML completo
                logger.info(f"Buscando XML completo | Chave: {resumo.chave_acesso[:10]}... | "
                          f"Tentativa: {resumo.tentativas + 1} | Empresa: {empresa.razao_social}")
                
                xml_completo, sucesso = buscar_xml_completo_por_chave(
                    resumo.chave_acesso, empresa.cnpj, pfx_bytes, cert_senha, ambiente, uf_autor
                )
                
                resumo.tentativas += 1
                
                if sucesso and xml_completo:
                    # XML completo encontrado!
                    logger.info(f"XML completo encontrado! | Chave: {resumo.chave_acesso[:10]}...")
                    
                    # Atualiza nota no banco
                    nota = db.query(NotaFiscal).filter_by(chave_acesso=resumo.chave_acesso).first()
                    if nota:
                        nota.xml_conteudo = xml_completo
                        nota.status_manifesto = "COMPLETA"
                        # Extrai dados atualizados
                        info = extrair_dados_xml(xml_completo)
                        if info.get("numero"):
                            nota.numero_nfe = info.get("numero")
                        if info.get("serie") is not None:
                            nota.serie = info.get("serie")
                        try:
                            db.commit()
                            logger.info(f"Nota atualizada para COMPLETA | Chave: {resumo.chave_acesso[:10]}...")
                        except Exception as e:
                            db.rollback()
                            logger.error(
                                f"Erro ao atualizar nota para COMPLETA | Chave: {resumo.chave_acesso[:10]}... | Erro: {e}"
                            )
                            resumo.status = 'erro'
                            resumo.updated_at = datetime.now()
                            db.commit()
                            erros += 1
                            continue
                    else:
                        # Se não existe nota, cria uma nova
                        info = extrair_dados_xml(xml_completo)
                        nova_nota = NotaFiscal(
                            empresa_id=empresa.id,
                            chave_acesso=resumo.chave_acesso,
                            numero_nfe=info.get("numero"),
                            serie=info.get("serie"),
                            emitente_nome=info.get("emitente", "Desconhecido"),
                            emitente_cnpj=info.get("emitente_cnpj"),
                            natureza_operacao=info.get("natureza_operacao"),
                            valor_total=info.get("valor", 0),
                            data_emissao=info.get("data") or datetime.now(),
                            xml_conteudo=xml_completo,
                            status_manifesto="COMPLETA"
                        )
                        db.add(nova_nota)
                        try:
                            db.commit()
                            logger.info(f"Nota criada como COMPLETA | Chave: {resumo.chave_acesso[:10]}...")
                        except Exception as e:
                            db.rollback()
                            logger.error(
                                f"Erro ao criar nota COMPLETA | Chave: {resumo.chave_acesso[:10]}... | Erro: {e}"
                            )
                            resumo.status = 'erro'
                            resumo.updated_at = datetime.now()
                            db.commit()
                            erros += 1
                            continue
                    
                    # Marca resumo como completo
                    resumo.status = 'completo'
                    resumo.updated_at = datetime.now()
                    try:
                        db.commit()
                        completos += 1
                    except Exception as e:
                        db.rollback()
                        logger.error(
                            f"Erro ao marcar resumo como completo | Chave: {resumo.chave_acesso[:10]}... | Erro: {e}"
                        )
                        erros += 1
                        continue
                    
                else:
                    # XML completo ainda não disponível
                    if resumo.tentativas >= max_tentativas:
                        logger.warning(f"Máximo de tentativas atingido | Chave: {resumo.chave_acesso[:10]}... | "
                                     f"Marcando como erro")
                        resumo.status = 'erro'
                        resumo.updated_at = datetime.now()
                        db.commit()
                        erros += 1
                    else:
                        # Agenda próxima tentativa (backoff exponencial mais forte)
                        base_min = int(os.getenv("RESUMOS_BACKOFF_BASE_MIN", "10"))
                        max_min = int(os.getenv("RESUMOS_BACKOFF_MAX_MIN", "360"))
                        intervalo_minutos = min(max_min, base_min * (2 ** min(resumo.tentativas - 1, 6)))
                        resumo.data_proxima_tentativa = datetime.now() + timedelta(minutes=intervalo_minutos)
                        resumo.status = 'pendente'
                        resumo.updated_at = datetime.now()
                        db.commit()
                        logger.info(
                            f"XML completo ainda não disponível | Chave: {resumo.chave_acesso[:10]}... | "
                            f"Próxima tentativa em {intervalo_minutos} minutos"
                        )
                        processados += 1
                
                # Aguarda um pouco antes de processar próximo
                time.sleep(2)
                
            except Exception as e:
                logger.error(f"Erro ao processar resumo pendente | ID: {resumo.id} | Chave: {resumo.chave_acesso[:10]}... | Erro: {e}")
                resumo.status = 'erro'
                resumo.updated_at = datetime.now()
                db.commit()
                erros += 1
        
        logger.info(f"Processamento concluído | Processados: {processados} | Completos: {completos} | Erros: {erros}")
        
    except Exception as e:
        logger.error(f"Erro crítico no processamento de resumos pendentes | Erro: {e}")
        import traceback
        logger.error(traceback.format_exc())
    finally:
        db.close()
    
    logger.info("=" * 80)
    logger.info("FIM DO PROCESSAMENTO DE RESUMOS PENDENTES")
    logger.info("=" * 80)

if __name__ == "__main__":
    processar_resumos_pendentes()
