"""Configuração de ambiente compartilhada (sem segredos no código)."""
import os

from dotenv import load_dotenv

load_dotenv()


def is_production() -> bool:
    return (os.getenv("ENV") or "").lower() in ("production", "prod")


def site_url() -> str:
    return (os.getenv("SITE_URL") or "https://www.beatlexml.com.br").rstrip("/")


def contato_email() -> str:
    return os.getenv("CONTATO_EMAIL") or "contato@beatlexml.com.br"


def empresa_razao() -> str:
    return (os.getenv("EMPRESA_RAZAO") or "BeatleXML").strip()


def empresa_cnpj() -> str:
    digitos = "".join(c for c in (os.getenv("EMPRESA_CNPJ") or "") if c.isdigit())
    if len(digitos) != 14:
        return ""
    return f"{digitos[:2]}.{digitos[2:5]}.{digitos[5:8]}/{digitos[8:12]}-{digitos[12:]}"


def empresa_cidade() -> str:
    return (os.getenv("EMPRESA_CIDADE") or "Mangueirinha, PR").strip()


def recaptcha_site_key() -> str:
    return (os.getenv("RECAPTCHA_SITE_KEY") or "").strip()


def recaptcha_secret_key() -> str:
    return (os.getenv("RECAPTCHA_SECRET_KEY") or "").strip()


def recaptcha_min_score() -> float:
    try:
        return float(os.getenv("RECAPTCHA_MIN_SCORE") or "0.5")
    except ValueError:
        return 0.5


def ga_measurement_id() -> str:
    return (os.getenv("GA_MEASUREMENT_ID") or "").strip()


def smtp_host() -> str:
    return (os.getenv("SMTP_HOST") or "").strip()


def smtp_port() -> int:
    try:
        return int(os.getenv("SMTP_PORT") or "465")
    except ValueError:
        return 465


def smtp_user() -> str:
    return (os.getenv("SMTP_USER") or "").strip()


def smtp_password() -> str:
    return os.getenv("SMTP_PASSWORD") or ""


def smtp_from() -> str:
    return (os.getenv("SMTP_FROM") or smtp_user()).strip()


def smtp_from_name() -> str:
    return (os.getenv("SMTP_FROM_NAME") or "BeatleXML").strip()


def mail_suporte() -> str:
    return (os.getenv("MAIL_SUPORTE") or contato_email()).strip()


def smtp_timeout() -> int:
    try:
        return int(os.getenv("SMTP_TIMEOUT") or "20")
    except ValueError:
        return 20


def smtp_configurado() -> bool:
    return bool(smtp_host())
