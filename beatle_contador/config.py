"""Configuração local do sincronizador. Sem certificado."""
import json
import os
from pathlib import Path

CONFIG_DIR = Path.home() / ".beatle_contador"
CONFIG_FILE = CONFIG_DIR / "config.json"
DEFAULT_BASE_URL = os.getenv("BEATLE_API_URL", "http://localhost:8000")
VERSAO = "1.2.0"


def load_config():
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(token: str, base_url: str = DEFAULT_BASE_URL, email: str = ""):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    atual = load_config()
    dados = {
        "token": token,
        "base_url": base_url or DEFAULT_BASE_URL,
        "versao": VERSAO,
    }
    if email:
        dados["email"] = email
    elif atual.get("email"):
        dados["email"] = atual["email"]
    CONFIG_FILE.write_text(json.dumps(dados, indent=2), encoding="utf-8")


def clear_session():
    """Remove o token e mantém servidor e e-mail para o próximo login."""
    atual = load_config()
    atual.pop("token", None)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(atual, indent=2), encoding="utf-8")
