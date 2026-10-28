import re
import xml.etree.ElementTree as ET
from typing import Optional


def decodificar_xml_bytes(raw: bytes) -> str:
    """Decodifica bytes de XML (UTF-8 com/sem BOM, UTF-8, Latin-1)."""
    if not raw:
        return ""
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return normalizar_conteudo_xml(raw.decode(encoding))
        except UnicodeDecodeError:
            continue
    return normalizar_conteudo_xml(raw.decode("utf-8", errors="replace"))


def normalizar_conteudo_xml(conteudo: str) -> str:
    """Remove BOM, declaração XML opcional e espaços extras (exportações de e-mail/portal)."""
    if not conteudo:
        return ""
    texto = conteudo.replace("\ufeff", "").strip()
    if texto.startswith("<?xml"):
        fim = texto.find("?>")
        if fim != -1:
            texto = texto[fim + 2 :].strip()
    return texto


class XMLParser:
    def __init__(self, xml_content):
        if not xml_content:
            self.root = None
            return
        try:
            self.xml_str = normalizar_conteudo_xml(xml_content)
            self.root = ET.fromstring(self.xml_str)
            self.ns = {'nfe': 'http://www.portalfiscal.inf.br/nfe'}
        except Exception:
            self.root = None

    def identificar_tipo(self):
        """Identifica se o XML é um resumo (resNFe) ou processado (procNFe)[cite: 80, 208]."""
        if not self.root: return "VAZIO"
        if 'resNFe' in self.xml_str: return "RESUMO"
        if 'procNFe' in self.xml_str or 'infNFe' in self.xml_str: return "COMPLETO"
        return "OUTRO"

    def _achar_inf_nfe(self):
        """Retorna o elemento infNFe com ou sem namespace (compatível com procNFe sem prefixo nfe:)."""
        if not self.root:
            return None
        inf_nfe = self.root.find(".//nfe:infNFe", self.ns)
        if inf_nfe is not None:
            return inf_nfe
        return self.root.find(".//infNFe")

    def extrair_destinatario_cnpj(self) -> Optional[str]:
        """Retorna o CNPJ ou CPF do destinatário (14 dígitos), ou None se resumo ou não encontrado."""
        inf_nfe = self._achar_inf_nfe()
        if inf_nfe is None:
            return None
        dest = inf_nfe.find("nfe:dest", self.ns) or inf_nfe.find("dest")
        if dest is None:
            return None
        cnpj = (dest.findtext("nfe:CNPJ", default="", namespaces=self.ns) or dest.findtext("CNPJ", default="") or "").strip()
        cpf = (dest.findtext("nfe:CPF", default="", namespaces=self.ns) or dest.findtext("CPF", default="") or "").strip()
        num = (cnpj or cpf).replace(".", "").replace("/", "").replace("-", "")
        if not num or not num.isdigit():
            return None
        return num.zfill(14)[:14]

    def extrair_destinatario_nome(self) -> Optional[str]:
        """Retorna o nome do destinatário (xNome), ou None se não encontrado."""
        inf_nfe = self._achar_inf_nfe()
        if inf_nfe is None:
            return None
        dest = inf_nfe.find("nfe:dest", self.ns) or inf_nfe.find("dest")
        if dest is None:
            return None
        nome = (dest.findtext("nfe:xNome", default="", namespaces=self.ns) or dest.findtext("xNome", default="") or "").strip()
        return nome or None

    def extrair_emitente_cnpj(self) -> Optional[str]:
        """Retorna o CNPJ ou CPF do emitente (14 dígitos), ou None se resumo ou não encontrado."""
        inf_nfe = self._achar_inf_nfe()
        if inf_nfe is None:
            return None
        emit = inf_nfe.find("nfe:emit", self.ns) or inf_nfe.find("emit")
        if emit is None:
            return None
        cnpj = (emit.findtext("nfe:CNPJ", default="", namespaces=self.ns) or emit.findtext("CNPJ", default="") or "").strip()
        cpf = (emit.findtext("nfe:CPF", default="", namespaces=self.ns) or emit.findtext("CPF", default="") or "").strip()
        num = (cnpj or cpf).replace(".", "").replace("/", "").replace("-", "")
        if not num or not num.isdigit():
            return None
        return num.zfill(14)[:14]

    def extrair_modelo(self) -> Optional[str]:
        """Retorna o modelo do documento: 55 (NFe) ou 65 (NFCe), ou None se não encontrado."""
        inf_nfe = self._achar_inf_nfe()
        if inf_nfe is not None:
            ide = inf_nfe.find("nfe:ide", self.ns) or inf_nfe.find("ide")
            if ide is not None:
                mod = (ide.findtext("nfe:mod", namespaces=self.ns) or ide.findtext("mod") or "").strip()
                if mod in ("55", "65"):
                    return mod
        chave = re.search(r'chNFe="(\d+)"|chNFe>(\d+)<|Id="NFe(\d+)"', (self.xml_str or ""))
        if chave:
            c = (chave.group(1) or chave.group(2) or chave.group(3) or "")
            if len(c) >= 8:
                modelo_chave = c[6:8]
                if modelo_chave in ("55", "65"):
                    return modelo_chave
        return None

    def extrair_detalhes_completos(self):
        """Extrai cabeçalho, emitente, totais e impostos detalhados."""
        if not self.root: return None

        # Tenta localizar a tag principal da NF-e
        infNfe = self.root.find('.//nfe:infNFe', self.ns)
        if infNfe is None: return None

        # Dados do Cabeçalho e Identificação
        emit = infNfe.find('nfe:emit', self.ns)
        ide = infNfe.find('nfe:ide', self.ns)
        total = infNfe.find('.//nfe:total/nfe:ICMSTot', self.ns)

        # Extração de Itens (Produtos) com tributação por item
        produtos = []
        detalhes = self.root.findall('.//nfe:det', self.ns)
        for det in detalhes:
            prod = det.find('nfe:prod', self.ns)
            imposto = det.find('nfe:imposto', self.ns)
            
            # Campos obrigatórios
            codigo = prod.findtext('nfe:cProd', namespaces=self.ns) or ''
            descricao = prod.findtext('nfe:xProd', namespaces=self.ns) or ''
            unidade = prod.findtext('nfe:uCom', namespaces=self.ns) or ''
            quantidade = float(prod.findtext('nfe:qCom', namespaces=self.ns) or 0)
            valor_unitario = float(prod.findtext('nfe:vUnCom', namespaces=self.ns) or 0)
            valor_total = float(prod.findtext('nfe:vProd', namespaces=self.ns) or 0)
            
            # ICMS
            icms = imposto.find('nfe:ICMS/nfe:ICMS00', self.ns) or imposto.find('nfe:ICMS/nfe:ICMS10', self.ns) or imposto.find('nfe:ICMS/nfe:ICMS20', self.ns) or imposto.find('nfe:ICMS/nfe:ICMS30', self.ns) or imposto.find('nfe:ICMS/nfe:ICMS40', self.ns) or imposto.find('nfe:ICMS/nfe:ICMS51', self.ns) or imposto.find('nfe:ICMS/nfe:ICMS60', self.ns) or imposto.find('nfe:ICMS/nfe:ICMS70', self.ns) or imposto.find('nfe:ICMS/nfe:ICMS90', self.ns) or imposto.find('nfe:ICMS/nfe:ICMSPart', self.ns) or imposto.find('nfe:ICMS/nfe:ICMSST', self.ns) or imposto.find('nfe:ICMS/nfe:ICMSSN101', self.ns) or imposto.find('nfe:ICMS/nfe:ICMSSN102', self.ns) or imposto.find('nfe:ICMS/nfe:ICMSSN201', self.ns) or imposto.find('nfe:ICMS/nfe:ICMSSN202', self.ns) or imposto.find('nfe:ICMS/nfe:ICMSSN500', self.ns) or imposto.find('nfe:ICMS/nfe:ICMSSN900', self.ns)
            v_icms = imposto.findtext('.//nfe:vICMS', default="0.00", namespaces=self.ns)
            v_icms_st = imposto.findtext('.//nfe:vICMSST', default="0.00", namespaces=self.ns)
            v_fcp = imposto.findtext('.//nfe:vFCP', default="0.00", namespaces=self.ns)
            bc_icms = icms.findtext('nfe:vBC', default="0.00", namespaces=self.ns) if icms is not None else "0.00"
            aliq_icms = icms.findtext('nfe:pICMS', default="0.00", namespaces=self.ns) if icms is not None else "0.00"
            cst_icms = icms.findtext('nfe:CST', default="", namespaces=self.ns) or icms.findtext('nfe:CSOSN', default="", namespaces=self.ns) if icms is not None else ""
            
            # IPI
            ipi = imposto.find('nfe:IPI/nfe:IPITrib', self.ns) or imposto.find('nfe:IPI/nfe:IPINT', self.ns)
            v_ipi = imposto.findtext('.//nfe:vIPI', default="0.00", namespaces=self.ns)
            aliq_ipi = ipi.findtext('nfe:pIPI', default="0.00", namespaces=self.ns) if ipi is not None else "0.00"
            cst_ipi = ipi.findtext('nfe:CST', default="", namespaces=self.ns) if ipi is not None else ""
            
            # Outros campos
            ncm = prod.findtext('nfe:NCM', namespaces=self.ns) or ''
            cfop = prod.findtext('nfe:CFOP', namespaces=self.ns) or ''
            codigo_barras = prod.findtext('nfe:cEAN', namespaces=self.ns) or prod.findtext('nfe:cEANTrib', namespaces=self.ns) or ''
            
            # Desconto e despesas acessórias
            v_desc = prod.findtext('nfe:vDesc', default="0.00", namespaces=self.ns)
            v_frete = det.findtext('.//nfe:vFrete', default="0.00", namespaces=self.ns)
            v_seg = det.findtext('.//nfe:vSeg', default="0.00", namespaces=self.ns)
            v_outro = det.findtext('.//nfe:vOutro', default="0.00", namespaces=self.ns)
            v_despesas = float(v_frete) + float(v_seg) + float(v_outro)
            
            produtos.append({
                'n_item': det.get('nItem'),
                'codigo': codigo,
                'descricao': descricao,
                'unidade': unidade,
                'quantidade': quantidade,
                'valor_unitario': valor_unitario,
                'valor_total': valor_total,
                'imposto_icms': float(v_icms),
                'imposto_ipi': float(v_ipi),
                # Campos adicionais obrigatórios
                'cfop': cfop,
                'bc_icms': float(bc_icms),
                'aliq_icms': float(aliq_icms),
                'aliq_ipi': float(aliq_ipi),
                # Campos opcionais (mostrar abaixo da descrição se existir)
                'ncm': ncm,
                'cst_icms': cst_icms,
                'cst_ipi': cst_ipi,
                'v_icms_st': float(v_icms_st),
                'v_fcp': float(v_fcp),
                'v_desconto': float(v_desc),
                'v_despesas': v_despesas,
                'codigo_barras': codigo_barras
            })

        nat_op = ""
        if ide is not None:
            nat_op = (
                ide.findtext('nfe:natOp', namespaces=self.ns)
                or ide.findtext('natOp')
                or ""
            ).strip()

        return {
            "nfe_info": {
                "numero": ide.findtext('nfe:nNF', namespaces=self.ns) if ide is not None else None,
                "serie": ide.findtext('nfe:serie', namespaces=self.ns) if ide is not None else None,
                "data_emissao": ide.findtext('nfe:dhEmi', namespaces=self.ns) if ide is not None else None,
                "data_saida": ide.findtext('nfe:dhSaiEnt', namespaces=self.ns) if ide is not None else None,
                "natureza_operacao": nat_op or None,
                "valor_nfe": float(total.findtext('nfe:vNF', namespaces=self.ns) or 0) if total is not None else 0,
                "valor_icms_total": float(total.findtext('nfe:vICMS', namespaces=self.ns) or 0) if total is not None else 0,
            },
            "emitente": {
                "cnpj": emit.findtext('nfe:CNPJ', namespaces=self.ns) if emit is not None else None,
                "nome": emit.findtext('nfe:xNome', namespaces=self.ns) if emit is not None else None,
                "ie": emit.findtext('nfe:IE', namespaces=self.ns) if emit is not None else None,
            },
            "produtos": produtos
        }

    def extrair_todos_os_dados(self):
        """Função de compatibilidade para o worker.py gravar no banco."""
        if self.root is None:
            return {"numero": None, "emitente": "Erro no XML", "valor": 0, "data": None, "tipo_operacao": "Erro"}
        
        tipo = self.identificar_tipo()
        if tipo == "RESUMO":
            ns_res = "{http://www.portalfiscal.inf.br/nfe}"
            emitente = (
                self.root.findtext(f".//{ns_res}xNome")
                or self.root.findtext(".//xNome")
                or "Desconhecido"
            )
            vnf = self.root.findtext(f".//{ns_res}vNF") or self.root.findtext(".//vNF") or "0"
            valor = float(vnf) if vnf else 0
            dh_emi = self.root.findtext(f".//{ns_res}dhEmi") or self.root.findtext(".//dhEmi")
            return {
                "numero": "Resumo",
                "emitente": emitente.strip() or "Desconhecido",
                "valor": valor,
                "data": dh_emi,
                "tipo_operacao": "RESUMO",
            }
            
        infNfe = self._achar_inf_nfe()
        if infNfe is not None:
            ide = infNfe.find('nfe:ide', self.ns) or infNfe.find('ide')
            emit = infNfe.find('nfe:emit', self.ns) or infNfe.find('emit')
            total = infNfe.find('.//nfe:total/nfe:ICMSTot', self.ns) or infNfe.find('.//total/ICMSTot')
            n_num = (ide.findtext('nfe:nNF', namespaces=self.ns) if ide is not None else None) or (ide.findtext('nNF') if ide is not None else None)
            serie_val = (ide.findtext('nfe:serie', namespaces=self.ns) if ide is not None else None) or (ide.findtext('serie') if ide is not None else None)
            nat_op = (ide.findtext('nfe:natOp', namespaces=self.ns) if ide is not None else None) or (ide.findtext('natOp') if ide is not None else None)
            x_nome = (emit.findtext('nfe:xNome', namespaces=self.ns) if emit is not None else None) or (emit.findtext('xNome') if emit is not None else None)
            v_nf = (total.findtext('nfe:vNF', namespaces=self.ns) if total is not None else None) or (total.findtext('vNF') if total is not None else None)
            dh_emi = (ide.findtext('nfe:dhEmi', namespaces=self.ns) if ide is not None else None) or (ide.findtext('dhEmi') if ide is not None else None)
            emit_cnpj = self.extrair_emitente_cnpj()
            return {
                "numero": n_num,
                "serie": (serie_val or "").strip() or None,
                "natureza_operacao": (nat_op or "").strip() or None,
                "emitente": (x_nome or "").strip() or "Desconhecido",
                "emitente_cnpj": emit_cnpj,
                "valor": float(v_nf or 0),
                "data": dh_emi,
                "tipo_operacao": "COMPLETA"
            }
        return {"numero": None, "emitente": "Desconhecido", "valor": 0, "data": None, "tipo_operacao": tipo}

    def extrair_pagamento(self):
        """Extrai cobr/dup e pag/detPag; devolve duplicatas, pagamentos e lista de parcelas prontas para persistência."""
        from datetime import datetime as dt
        if not self.root:
            return {"duplicatas": [], "pagamentos": [], "parcelas": []}
        inf_nfe = self.root.find(".//nfe:infNFe", self.ns)
        if inf_nfe is None:
            return {"duplicatas": [], "pagamentos": [], "parcelas": []}

        duplicatas = []
        cobr = inf_nfe.find("nfe:cobr", self.ns)
        if cobr is not None:
            for dup in cobr.findall("nfe:dup", self.ns):
                n_dup = dup.findtext("nfe:nDup", default="", namespaces=self.ns) or ""
                d_venc = dup.findtext("nfe:dVenc", default="", namespaces=self.ns) or ""
                v_dup = float(dup.findtext("nfe:vDup", default="0", namespaces=self.ns) or "0")
                duplicatas.append({"nDup": n_dup, "dVenc": d_venc, "vDup": v_dup})

        pagamentos = []
        pag = inf_nfe.find("nfe:pag", self.ns)
        if pag is not None:
            for det in pag.findall("nfe:detPag", self.ns):
                ind_pag = det.findtext("nfe:indPag", default="1", namespaces=self.ns) or "1"
                t_pag = det.findtext("nfe:tPag", default="99", namespaces=self.ns) or "99"
                v_pag = float(det.findtext("nfe:vPag", default="0", namespaces=self.ns) or "0")
                x_pag = det.findtext("nfe:xPag", default="", namespaces=self.ns) or ""
                d_pag = det.findtext("nfe:dPag", default="", namespaces=self.ns) or ""
                pagamentos.append({
                    "indPag": ind_pag, "tPag": t_pag, "vPag": v_pag, "xPag": x_pag, "dPag": d_pag
                })

        total = inf_nfe.find(".//nfe:total/nfe:ICMSTot", self.ns)
        valor_nf = float(total.findtext("nfe:vNF", default="0", namespaces=self.ns) or "0") if total is not None else 0
        ide = inf_nfe.find("nfe:ide", self.ns)
        dh_emi = ide.findtext("nfe:dhEmi", default="", namespaces=self.ns) or "" if ide is not None else ""

        def parse_date(s):
            if not s:
                return None
            s = s.strip()
            for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
                try:
                    return dt.strptime(s[:10], fmt)
                except Exception:
                    continue
            try:
                return dt.strptime(s[:10].replace("T", " "), "%Y-%m-%d")
            except Exception:
                return None

        tem_dpag = any(p.get("dPag") for p in pagamentos)
        primeira_forma = pagamentos[0] if pagamentos else None
        t_pag_default = primeira_forma.get("tPag", "99") if primeira_forma else "99"
        ind_pag_default = primeira_forma.get("indPag", "1") if primeira_forma else "1"

        parcelas = []
        if duplicatas:
            for i, dup in enumerate(duplicatas):
                d_venc = dup.get("dVenc") or ""
                data_venc = parse_date(d_venc)
                valor = dup.get("vDup", 0)
                n_dup = dup.get("nDup", str(i + 1))
                forma = t_pag_default
                ind_pag = ind_pag_default
                data_pag = None
                if primeira_forma and primeira_forma.get("dPag"):
                    data_pag = parse_date(primeira_forma.get("dPag"))
                editavel = not tem_dpag and data_pag is None
                parcelas.append({
                    "numero_parcela": n_dup,
                    "data_vencimento": data_venc,
                    "valor": valor,
                    "forma_pagamento": forma,
                    "forma_pagamento_label": _tpag_label(forma),
                    "ind_pag": ind_pag,
                    "data_pagamento": data_pag,
                    "origem_nfe": True,
                    "editavel": editavel,
                })
        else:
            data_venc = parse_date(dh_emi[:10]) if dh_emi else None
            forma = t_pag_default
            ind_pag = ind_pag_default
            data_pag = None
            if primeira_forma and primeira_forma.get("dPag"):
                data_pag = parse_date(primeira_forma.get("dPag"))
            editavel = not tem_dpag and data_pag is None
            parcelas.append({
                "numero_parcela": "1/1",
                "data_vencimento": data_venc,
                "valor": valor_nf,
                "forma_pagamento": forma,
                "forma_pagamento_label": _tpag_label(forma),
                "ind_pag": ind_pag,
                "data_pagamento": data_pag,
                "origem_nfe": bool(pagamentos or cobr),
                "editavel": editavel,
            })

        if not parcelas and valor_nf:
            parcelas.append({
                "numero_parcela": "1/1",
                "data_vencimento": parse_date(dh_emi[:10]) if dh_emi else None,
                "valor": valor_nf,
                "forma_pagamento": "99",
                "forma_pagamento_label": "Outros",
                "ind_pag": "1",
                "data_pagamento": None,
                "origem_nfe": False,
                "editavel": True,
            })

        return {"duplicatas": duplicatas, "pagamentos": pagamentos, "parcelas": parcelas}


# Mapeamento tPag (meio de pagamento) NFe 4.0 para rótulo amigável
TPAG_LABELS = {
    "01": "Dinheiro",
    "02": "Cheque",
    "03": "Cartão de Crédito",
    "04": "Cartão de Débito",
    "05": "Crédito Loja",
    "10": "Vale Alimentação",
    "11": "Vale Refeição",
    "12": "Vale Presente",
    "13": "Vale Combustível",
    "15": "Boleto Bancário",
    "16": "Depósito Bancário",
    "17": "PIX",
    "18": "Transferência Bancária",
    "19": "Programa de Fidelidade",
    "90": "Sem Pagamento",
    "99": "Outros",
}


def _tpag_label(codigo):
    """Retorna rótulo do meio de pagamento; codigo pode ser str ou int."""
    key = str(codigo).zfill(2) if codigo is not None else "99"
    return TPAG_LABELS.get(key, "Outros")


def extrair_pagamento_xml(xml_content):
    """
    Extrai dados de cobrança e pagamento do XML da NF-e (cobr/dup e pag/detPag).
    Retorno: {
        "duplicatas": [ {"nDup", "dVenc", "vDup"}, ... ],
        "pagamentos": [ {"indPag", "tPag", "vPag", "xPag", "dPag"}, ... ],
        "parcelas": [ {"numero_parcela", "data_vencimento", "valor", "forma_pagamento", "forma_pagamento_label", "ind_pag", "data_pagamento", "editavel"}, ... ]
    }
    parcelas: uma por duplicata; se não houver dup, uma linha com total e forma do detPag; editavel=True só quando não veio dPag da NFe.
    """
    parser = XMLParser(xml_content)
    return parser.extrair_pagamento() if parser.root else {"duplicatas": [], "pagamentos": [], "parcelas": []}


def extrair_dados_xml(xml_content):
    parser = XMLParser(xml_content)
    return parser.extrair_todos_os_dados()