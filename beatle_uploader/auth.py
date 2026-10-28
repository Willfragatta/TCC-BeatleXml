"""Autenticação e chamadas à API do Beatle Uploader."""
import requests
from typing import Optional, List, Dict

from .config import DEFAULT_BASE_URL, load_config


def login(base_url: str, email: str, senha: str) -> Dict:
    """
    Faz login na API e retorna {"token": str, "empresas": [{id, razao_social, cnpj}]}.
    Levanta requests.HTTPError em caso de erro.
    """
    url = f"{base_url.rstrip('/')}/api/uploader/login"
    r = requests.post(url, json={"email": email, "senha": senha}, timeout=30)
    r.raise_for_status()
    return r.json()


def upload_xmls(base_url: str, token: str, empresa_id: int, arquivos: List[tuple]) -> Dict:
    """
    Envia XMLs para a API.
    arquivos: lista de (nome_arquivo, bytes_conteudo)
    Retorna {"importados": int, "erros": int}.
    """
    url = f"{base_url.rstrip('/')}/api/uploader/upload"
    files = [("arquivos", (nome, conteudo)) for nome, conteudo in arquivos]
    data = {"empresa_id": empresa_id}
    r = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}"},
        data=data,
        files=files,
        timeout=120,
    )
    r.raise_for_status()
    return r.json()


def heartbeat(base_url: str, token: str, empresa_id: int, pasta: Optional[str] = None, versao: str = "1.0.0") -> None:
    """Envia heartbeat para o servidor."""
    url = f"{base_url.rstrip('/')}/api/uploader/heartbeat"
    payload = {"empresa_id": empresa_id, "pasta_monitorada": pasta, "versao": versao}
    r = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
        timeout=10,
    )
    r.raise_for_status()
