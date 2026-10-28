"""Carga sintética da demonstração. Pode rodar de novo: se o suporte já existe, não duplica."""
import os
import sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from dotenv import load_dotenv

load_dotenv(RAIZ / ".env")

from app.core.auth import hash_senha
from app.models.database import (
    Empresa,
    Etiqueta,
    NotaEtiqueta,
    NotaFiscal,
    SessionLocal,
    SuporteLog,
    Usuario,
    UsuarioEmpresa,
)
from app.services.parcelas import gravar_parcelas

CNPJ_MATRIZ = "11222333000181"
CNPJ_FILIAL = "11222333000262"
CNPJ_PAPELARIA = "33444555000181"
CNPJ_PADARIA = "99888777000100"
CNPJ_FORNECEDOR = "55666777000181"

EMAIL_SUPORTE = "suporte@demo.local"


def _dv_chave(corpo43: str) -> str:
    peso = 2
    soma = 0
    for digito in reversed(corpo43):
        soma += int(digito) * peso
        peso = 2 if peso == 9 else peso + 1
    resto = soma % 11
    return "0" if resto in (0, 1) else str(11 - resto)


def _chave(cnpj: str, modelo: str, numero: int, cnf: str) -> str:
    corpo = f"41{ '2610' }{cnpj}{modelo}001{numero:09d}1{cnf}"
    if len(corpo) != 43:
        raise RuntimeError(f"chave com {len(corpo)} dígitos: {corpo}")
    return corpo + _dv_chave(corpo)


def _xml_nfe(chave: str, emit_cnpj: str, emit_nome: str, dest_cnpj: str, dest_nome: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<nfeProc versao="4.00" xmlns="http://www.portalfiscal.inf.br/nfe">
  <NFe xmlns="http://www.portalfiscal.inf.br/nfe">
    <infNFe Id="NFe{chave}" versao="4.00">
      <ide>
        <cUF>41</cUF>
        <cNF>{chave[35:43]}</cNF>
        <natOp>VENDA DE MERCADORIA</natOp>
        <mod>55</mod>
        <serie>1</serie>
        <nNF>42</nNF>
        <dhEmi>2026-10-15T10:30:00-03:00</dhEmi>
        <tpNF>1</tpNF>
        <idDest>1</idDest>
        <cMunFG>4114302</cMunFG>
        <tpImp>1</tpImp>
        <tpEmis>1</tpEmis>
        <cDV>{chave[-1]}</cDV>
        <tpAmb>2</tpAmb>
        <finNFe>1</finNFe>
        <indFinal>0</indFinal>
        <indPres>0</indPres>
        <procEmi>0</procEmi>
        <verProc>DEMO</verProc>
      </ide>
      <emit>
        <CNPJ>{emit_cnpj}</CNPJ>
        <xNome>{emit_nome}</xNome>
        <enderEmit>
          <xLgr>Rua das Industrias</xLgr>
          <nro>100</nro>
          <xBairro>Centro</xBairro>
          <cMun>4114302</cMun>
          <xMun>Palmas</xMun>
          <UF>PR</UF>
          <CEP>85555000</CEP>
          <cPais>1058</cPais>
          <xPais>BRASIL</xPais>
        </enderEmit>
        <IE>1234567890</IE>
        <CRT>1</CRT>
      </emit>
      <dest>
        <CNPJ>{dest_cnpj}</CNPJ>
        <xNome>{dest_nome}</xNome>
        <enderDest>
          <xLgr>Avenida Brasil</xLgr>
          <nro>250</nro>
          <xBairro>Centro</xBairro>
          <cMun>4114302</cMun>
          <xMun>Palmas</xMun>
          <UF>PR</UF>
          <CEP>85555000</CEP>
          <cPais>1058</cPais>
          <xPais>BRASIL</xPais>
        </enderDest>
        <indIEDest>1</indIEDest>
        <IE>0987654321</IE>
      </dest>
      <det nItem="1">
        <prod>
          <cProd>1</cProd>
          <cEAN>SEM GTIN</cEAN>
          <xProd>CAIXA DE MERENDA ESCOLAR</xProd>
          <NCM>19059090</NCM>
          <CFOP>5102</CFOP>
          <uCom>UN</uCom>
          <qCom>10.0000</qCom>
          <vUnCom>150.0000000000</vUnCom>
          <vProd>1500.00</vProd>
          <cEANTrib>SEM GTIN</cEANTrib>
          <uTrib>UN</uTrib>
          <qTrib>10.0000</qTrib>
          <vUnTrib>150.0000000000</vUnTrib>
          <indTot>1</indTot>
        </prod>
        <imposto>
          <ICMS><ICMSSN102><orig>0</orig><CSOSN>102</CSOSN></ICMSSN102></ICMS>
          <PIS><PISOutr><CST>99</CST><vBC>0.00</vBC><pPIS>0.00</pPIS><vPIS>0.00</vPIS></PISOutr></PIS>
          <COFINS><COFINSOutr><CST>99</CST><vBC>0.00</vBC><pCOFINS>0.00</pCOFINS><vCOFINS>0.00</vCOFINS></COFINSOutr></COFINS>
        </imposto>
      </det>
      <total>
        <ICMSTot>
          <vBC>0.00</vBC><vICMS>0.00</vICMS><vICMSDeson>0.00</vICMSDeson><vFCP>0.00</vFCP>
          <vBCST>0.00</vBCST><vST>0.00</vST><vFCPST>0.00</vFCPST><vFCPSTRet>0.00</vFCPSTRet>
          <vProd>1500.00</vProd><vFrete>0.00</vFrete><vSeg>0.00</vSeg><vDesc>0.00</vDesc>
          <vII>0.00</vII><vIPI>0.00</vIPI><vIPIDevol>0.00</vIPIDevol>
          <vPIS>0.00</vPIS><vCOFINS>0.00</vCOFINS><vOutro>0.00</vOutro><vNF>1500.00</vNF>
        </ICMSTot>
      </total>
      <transp><modFrete>9</modFrete></transp>
      <cobr>
        <fat><nFat>42</nFat><vOrig>1500.00</vOrig><vDesc>0.00</vDesc><vLiq>1500.00</vLiq></fat>
        <dup><nDup>001</nDup><dVenc>2026-11-20</dVenc><vDup>900.00</vDup></dup>
        <dup><nDup>002</nDup><dVenc>2026-12-20</dVenc><vDup>600.00</vDup></dup>
      </cobr>
      <pag>
        <detPag><indPag>1</indPag><tPag>15</tPag><vPag>1500.00</vPag></detPag>
      </pag>
      <infAdic><infCpl>Documento ficticio para a demonstracao da banca. Nao tem valor fiscal.</infCpl></infAdic>
    </infNFe>
  </NFe>
  <protNFe versao="4.00">
    <infProt>
      <tpAmb>2</tpAmb>
      <verAplic>DEMO</verAplic>
      <chNFe>{chave}</chNFe>
      <dhRecbto>2026-10-15T10:31:00-03:00</dhRecbto>
      <nProt>141260000000042</nProt>
      <digVal>AAAAAAAAAAAAAAAAAAAAAA==</digVal>
      <cStat>100</cStat>
      <xMotivo>Autorizado o uso da NF-e</xMotivo>
    </infProt>
  </protNFe>
</nfeProc>
"""


def _xml_nfce(chave: str, emit_cnpj: str, emit_nome: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<nfeProc versao="4.00" xmlns="http://www.portalfiscal.inf.br/nfe">
  <NFe xmlns="http://www.portalfiscal.inf.br/nfe">
    <infNFe Id="NFe{chave}" versao="4.00">
      <ide>
        <cUF>41</cUF>
        <cNF>{chave[35:43]}</cNF>
        <natOp>VENDA AO CONSUMIDOR</natOp>
        <mod>65</mod>
        <serie>1</serie>
        <nNF>15</nNF>
        <dhEmi>2026-10-20T14:05:00-03:00</dhEmi>
        <tpNF>1</tpNF>
        <idDest>1</idDest>
        <cMunFG>4114302</cMunFG>
        <tpImp>4</tpImp>
        <tpEmis>1</tpEmis>
        <cDV>{chave[-1]}</cDV>
        <tpAmb>2</tpAmb>
        <finNFe>1</finNFe>
        <indFinal>1</indFinal>
        <indPres>1</indPres>
        <procEmi>0</procEmi>
        <verProc>DEMO</verProc>
      </ide>
      <emit>
        <CNPJ>{emit_cnpj}</CNPJ>
        <xNome>{emit_nome}</xNome>
        <enderEmit>
          <xLgr>Avenida Brasil</xLgr>
          <nro>250</nro>
          <xBairro>Centro</xBairro>
          <cMun>4114302</cMun>
          <xMun>Palmas</xMun>
          <UF>PR</UF>
          <CEP>85555000</CEP>
          <cPais>1058</cPais>
          <xPais>BRASIL</xPais>
        </enderEmit>
        <IE>0987654321</IE>
        <CRT>1</CRT>
      </emit>
      <dest>
        <xNome>CLIENTE DE BALCAO</xNome>
        <indIEDest>9</indIEDest>
      </dest>
      <det nItem="1">
        <prod>
          <cProd>15</cProd>
          <cEAN>SEM GTIN</cEAN>
          <xProd>PAO FRANCES</xProd>
          <NCM>19059090</NCM>
          <CFOP>5102</CFOP>
          <uCom>UN</uCom>
          <qCom>10.0000</qCom>
          <vUnCom>3.5900000000</vUnCom>
          <vProd>35.90</vProd>
          <cEANTrib>SEM GTIN</cEANTrib>
          <uTrib>UN</uTrib>
          <qTrib>10.0000</qTrib>
          <vUnTrib>3.5900000000</vUnTrib>
          <indTot>1</indTot>
        </prod>
        <imposto>
          <ICMS><ICMSSN102><orig>0</orig><CSOSN>102</CSOSN></ICMSSN102></ICMS>
          <PIS><PISOutr><CST>99</CST><vBC>0.00</vBC><pPIS>0.00</pPIS><vPIS>0.00</vPIS></PISOutr></PIS>
          <COFINS><COFINSOutr><CST>99</CST><vBC>0.00</vBC><pCOFINS>0.00</pCOFINS><vCOFINS>0.00</vCOFINS></COFINSOutr></COFINS>
        </imposto>
      </det>
      <total>
        <ICMSTot>
          <vBC>0.00</vBC><vICMS>0.00</vICMS><vICMSDeson>0.00</vICMSDeson><vFCP>0.00</vFCP>
          <vBCST>0.00</vBCST><vST>0.00</vST><vFCPST>0.00</vFCPST><vFCPSTRet>0.00</vFCPSTRet>
          <vProd>35.90</vProd><vFrete>0.00</vFrete><vSeg>0.00</vSeg><vDesc>0.00</vDesc>
          <vII>0.00</vII><vIPI>0.00</vIPI><vIPIDevol>0.00</vIPIDevol>
          <vPIS>0.00</vPIS><vCOFINS>0.00</vCOFINS><vOutro>0.00</vOutro><vNF>35.90</vNF>
        </ICMSTot>
      </total>
      <transp><modFrete>9</modFrete></transp>
      <pag>
        <detPag><indPag>0</indPag><tPag>01</tPag><vPag>35.90</vPag></detPag>
      </pag>
      <infAdic><infCpl>Cupom ficticio. O emitente e a loja, para o agente local aceitar o arquivo.</infCpl></infAdic>
    </infNFe>
  </NFe>
  <protNFe versao="4.00">
    <infProt>
      <tpAmb>2</tpAmb>
      <verAplic>DEMO</verAplic>
      <chNFe>{chave}</chNFe>
      <dhRecbto>2026-10-20T14:05:02-03:00</dhRecbto>
      <nProt>141260000000015</nProt>
      <digVal>BBBBBBBBBBBBBBBBBBBBBB==</digVal>
      <cStat>100</cStat>
      <xMotivo>Autorizado o uso da NF-e</xMotivo>
    </infProt>
  </protNFe>
</nfeProc>
"""


def gravar_fixture_nfce() -> Path:
    chave = _chave(CNPJ_MATRIZ, "65", 16, "87654321")
    pasta = RAIZ / "scripts" / "fixtures"
    pasta.mkdir(parents=True, exist_ok=True)
    destino = pasta / "nfce-caixa.xml"
    destino.write_text(
        _xml_nfce(chave, CNPJ_MATRIZ, "MERCADO BOM PRECO MATRIZ LTDA"),
        encoding="utf-8",
    )
    return destino


def _usuario(db, email, senha, nome, role, is_admin=False):
    usuario = Usuario(
        email=email,
        senha_hash=hash_senha(senha),
        nome_completo=nome,
        telefone="46999990000",
        status="ativo",
        status_pagamento="pago",
        status_assinatura="ativo",
        is_admin=is_admin,
        role=role,
        email_confirmado_em=datetime(2026, 10, 1, 9, 0),
        dois_fatores_email=False,
        created_at=datetime(2026, 10, 1, 9, 0),
    )
    db.add(usuario)
    db.flush()
    return usuario


def _empresa(db, dono, cnpj, razao):
    empresa = Empresa(
        usuario_id=dono.id,
        cnpj=cnpj,
        razao_social=razao,
        ambiente="2",
        uf_autor="41",
        status="aprovado",
        data_aprovacao=datetime(2026, 10, 1, 9, 0),
        data_ativacao=datetime(2026, 10, 1, 9, 0),
        ultimo_nsu="000000000000000",
        ultima_sync_mensagem="Sem certificado A1 nesta demonstração",
    )
    db.add(empresa)
    db.flush()
    return empresa


def _vinculo(db, usuario, empresa, papel):
    db.add(UsuarioEmpresa(usuario_id=usuario.id, empresa_id=empresa.id, papel=papel))


def carregar():
    gravar_fixture_nfce()
    db = SessionLocal()
    try:
        if db.query(Usuario).filter_by(email=EMAIL_SUPORTE).first():
            print("Carga de demonstração já existe. Nada foi duplicado.")
            return

        suporte = _usuario(db, EMAIL_SUPORTE, "Suporte#2026", "Suporte da Banca", "suporte", True)
        gestor = _usuario(db, "gestor@demo.local", "Gestor#2026", "Ana Gestora", "cliente")
        operador = _usuario(db, "operador@demo.local", "Operador#2026", "Bruno Operador", "cliente")
        leitura = _usuario(db, "leitura@demo.local", "Leitura#2026", "Clara Leitura", "cliente")
        contador = _usuario(db, "contador@demo.local", "Contador#2026", "Diego Contador", "contador")
        outro = _usuario(db, "outro@demo.local", "Outro#2026", "Elena Isolada", "cliente")

        matriz = _empresa(db, gestor, CNPJ_MATRIZ, "Mercado Bom Preço Matriz Ltda")
        filial = _empresa(db, gestor, CNPJ_FILIAL, "Mercado Bom Preço Filial Ltda")
        papelaria = _empresa(db, gestor, CNPJ_PAPELARIA, "Papelaria Folha Nova Ltda")
        padaria = _empresa(db, outro, CNPJ_PADARIA, "Padaria Isolada Ltda")

        _vinculo(db, gestor, matriz, "empresa_master")
        _vinculo(db, gestor, filial, "empresa_master")
        _vinculo(db, gestor, papelaria, "empresa_master")
        _vinculo(db, outro, padaria, "empresa_master")
        _vinculo(db, operador, matriz, "operador")
        _vinculo(db, leitura, matriz, "leitura")
        _vinculo(db, contador, matriz, "operador")
        _vinculo(db, contador, filial, "operador")
        _vinculo(db, contador, papelaria, "operador")

        chave_nfe = _chave(CNPJ_FORNECEDOR, "55", 42, "12345678")
        xml_nfe = _xml_nfe(
            chave_nfe,
            CNPJ_FORNECEDOR,
            "DISTRIBUIDORA HORIZONTE LTDA",
            CNPJ_MATRIZ,
            "MERCADO BOM PRECO MATRIZ LTDA",
        )
        nota = NotaFiscal(
            empresa_id=matriz.id,
            chave_acesso=chave_nfe,
            numero_nfe="42",
            serie="1",
            emitente_nome="DISTRIBUIDORA HORIZONTE LTDA",
            emitente_cnpj=CNPJ_FORNECEDOR,
            natureza_operacao="VENDA DE MERCADORIA",
            valor_total=1500,
            data_emissao=datetime(2026, 10, 15, 10, 30),
            status_manifesto="COMPLETA",
            xml_conteudo=xml_nfe,
            origem="RECEBIDA",
            modelo="55",
        )
        db.add(nota)
        db.flush()
        gravar_parcelas(db, nota)

        chave_nfce = _chave(CNPJ_MATRIZ, "65", 15, "11112222")
        xml_nfce = _xml_nfce(chave_nfce, CNPJ_MATRIZ, "MERCADO BOM PRECO MATRIZ LTDA")
        cupom = NotaFiscal(
            empresa_id=matriz.id,
            chave_acesso=chave_nfce,
            numero_nfe="15",
            serie="1",
            emitente_nome="CLIENTE DE BALCAO",
            emitente_cnpj=CNPJ_MATRIZ,
            natureza_operacao="VENDA AO CONSUMIDOR",
            valor_total=35.90,
            data_emissao=datetime(2026, 10, 20, 14, 5),
            status_manifesto="COMPLETA",
            xml_conteudo=xml_nfce,
            origem="EMITIDA",
            modelo="65",
        )
        db.add(cupom)
        db.flush()
        gravar_parcelas(db, cupom)

        etiqueta = Etiqueta(
            usuario_id=gestor.id,
            empresa_id=matriz.id,
            nome="Conferir",
            cor="#3B82F6",
            cor_fundo="#DBEAFE",
            cor_texto="#1E3A8A",
        )
        db.add(etiqueta)
        db.flush()
        db.add(NotaEtiqueta(nota_id=nota.id, etiqueta_id=etiqueta.id))
        db.add(SuporteLog(
            empresa_id=matriz.id,
            nivel="INFO",
            tipo="sistema",
            mensagem="Carga fictícia da demonstração aplicada. Nenhum certificado A1 foi gravado.",
        ))
        db.commit()
        print("Carga de demonstração gravada.")
        print(f"NF-e recebida: {chave_nfe}")
        print(f"NFC-e emitida: {chave_nfce}")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    if not os.getenv("DATABASE_URL"):
        print("DATABASE_URL ausente. Copie .env.example para .env ou suba o Docker Compose.")
        sys.exit(1)
    carregar()
