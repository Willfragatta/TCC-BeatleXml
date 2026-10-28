"""Configuração do Beatle Uploader."""
import os
import json
from pathlib import Path

CONFIG_DIR = Path.home() / ".beatle_uploader"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_BASE_URL = os.getenv("BEATLE_API_URL", "http://localhost:8000")


def _ensure_config_dir():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def load_config():
    """Carrega configuração salva."""
    if not CONFIG_FILE.exists():
        return {}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(
    token: str,
    empresa_id: int,
    pasta: str = "",
    base_url: str = DEFAULT_BASE_URL,
    pasta_nfe: str = "",
    pasta_nfce: str = "",
    **extra,
):
    """Salva token, empresa e pastas (uma geral ou NFe e NFCe separadas)."""
    _ensure_config_dir()
    prev = load_config()
    data = {
        "token": token,
        "empresa_id": empresa_id,
        "base_url": base_url.rstrip("/"),
        "pasta_nfe": (pasta_nfe or pasta or "").strip(),
        "pasta_nfce": (pasta_nfce or "").strip(),
    }
    # Compatibilidade: se só veio "pasta", usa como pasta_nfe
    if not data["pasta_nfe"] and pasta:
        data["pasta_nfe"] = pasta.strip()
    # Preserva / mescla campos ERP e outros
    for k in (
        "erp_enabled",
        "erp_host",
        "erp_port",
        "erp_db",
        "erp_user",
        "erp_password",
        "erp_intervalo_segundos",
        "cnpj_empresa",
        "empresas",  # lista [{id, razao_social, cnpj}] para ERP em todas as empresas
        "pasta_relatorios_cartao",  # extratos de credenciadoras enviados sozinhos
    ):
        if k in extra:
            data[k] = extra[k]
        elif k in prev:
            data[k] = prev[k]
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def update_config(**fields):
    """Atualiza campos pontuais no config.json."""
    _ensure_config_dir()
    data = load_config()
    data.update(fields)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def clear_config():
    """Remove configuração."""
    if CONFIG_FILE.exists():
        CONFIG_FILE.unlink()
