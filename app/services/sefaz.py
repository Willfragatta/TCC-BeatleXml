import base64
import tempfile
import os
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.hazmat.backends import default_backend
from requests import Session
from zeep import Client
from zeep.transports import Transport
from app.core.security import descriptografar

WSDL_PRODUCAO = "https://www1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx?WSDL"

def criar_sessao_ssl(cert_base64_cripto, senha_cripto):
    """Cria uma sessão HTTPS usando o certificado vindo do banco."""
    cert_pfx_base64 = descriptografar(cert_base64_cripto)
    cert_senha = descriptografar(senha_cripto).encode()
    pfx_data = base64.b64decode(cert_pfx_base64)

    # Extrai chave e certificado do PFX para o formato que o Requests entende
    private_key, certificate, additional_certificates = pkcs12.load_key_and_certificates(
        pfx_data, cert_senha, default_backend()
    )

    # Cria arquivos temporários para a conexão SSL (o Requests exige arquivos em disco)
    session = Session()
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pem") as cert_file, \
         tempfile.NamedTemporaryFile(delete=False, suffix=".pem") as key_file:
        
        # Aqui salvaríamos o cert e key formatados (faremos no próximo passo)
        pass 

    return session

def buscar_notas(empresa):
    print(f"🔍 Consultando SEFAZ para {empresa.cnpj} a partir do NSU {empresa.ultimo_nsu}...")
    # Lógica de loop de NSU entrará aqui
    return []