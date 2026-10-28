"""Chamadas ao servidor. A Receita é consultada lá, não neste programa."""
import requests

from .config import VERSAO


def _erro_http(resp: requests.Response) -> str:
    try:
        data = resp.json()
        detalhe = data.get("detail") if isinstance(data, dict) else None
        if isinstance(detalhe, list) and detalhe:
            detalhe = detalhe[0].get("msg") if isinstance(detalhe[0], dict) else detalhe[0]
        if detalhe:
            return str(detalhe)
    except Exception:
        pass
    texto = (resp.text or "").strip()
    if texto and len(texto) < 300:
        return texto
    return f"HTTP {resp.status_code}: {resp.reason}"


def _checar(resp: requests.Response) -> None:
    if resp.ok:
        return
    raise RuntimeError(_erro_http(resp))


def login(base_url: str, email: str, senha: str) -> dict:
    r = requests.post(
        f"{base_url.rstrip('/')}/api/contador/login",
        json={"email": email, "senha": senha},
        timeout=30,
    )
    _checar(r)
    return r.json()


def listar_empresas(base_url: str, token: str) -> list:
    r = requests.get(
        f"{base_url.rstrip('/')}/api/contador/empresas",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    _checar(r)
    data = r.json()
    return data if isinstance(data, list) else []


def heartbeat(base_url: str, token: str, empresa_id: int) -> None:
    r = requests.post(
        f"{base_url.rstrip('/')}/api/contador/heartbeat",
        headers={"Authorization": f"Bearer {token}"},
        json={"empresa_id": empresa_id, "versao": VERSAO},
        timeout=20,
    )
    _checar(r)


def sincronizar(base_url: str, token: str, empresa_id: int) -> dict:
    r = requests.post(
        f"{base_url.rstrip('/')}/api/contador/sincronizar/{empresa_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=180,
    )
    _checar(r)
    return r.json()


def sync_iniciar(base_url: str, token: str) -> dict:
    r = requests.post(
        f"{base_url.rstrip('/')}/api/contador/sync/iniciar",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    _checar(r)
    return r.json()


def sync_parar(base_url: str, token: str, retomar_automatico: bool = True) -> dict:
    r = requests.post(
        f"{base_url.rstrip('/')}/api/contador/sync/parar",
        headers={"Authorization": f"Bearer {token}"},
        json={"retomar_automatico": bool(retomar_automatico)},
        timeout=30,
    )
    _checar(r)
    return r.json()


def empresa_sync(
    base_url: str,
    token: str,
    empresa_id: int,
    ativo: bool,
    retomar_automatico: bool = True,
) -> dict:
    r = requests.post(
        f"{base_url.rstrip('/')}/api/contador/empresas/{empresa_id}/sync",
        headers={"Authorization": f"Bearer {token}"},
        json={"ativo": bool(ativo), "retomar_automatico": bool(retomar_automatico)},
        timeout=30,
    )
    _checar(r)
    return r.json()
