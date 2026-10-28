"""Validação reCAPTCHA v3 via SiteVerify (sem CreateAssessment/GCP)."""
import logging
from typing import Optional
from urllib.parse import urlparse

import requests
from fastapi import Request

from app.core import settings

logger = logging.getLogger("beatle_xml")

MSG_FALHA = "Não foi possível validar. Tente de novo."


def client_ip(request: Request) -> str:
    xff = request.headers.get("X-Forwarded-For") or ""
    if xff.strip():
        return xff.split(",")[0].strip()[:45]
    if request.client and request.client.host:
        return str(request.client.host)[:45]
    return "0.0.0.0"


def token_do_formulario(*candidatos: Optional[str]) -> str:
    for item in candidatos:
        if item and str(item).strip():
            return str(item).strip()
    return ""


def verificar_recaptcha(
    request: Request,
    token: Optional[str],
    acao_esperada: str,
) -> Optional[str]:
    """
    Retorna mensagem de erro ou None se ok.
    Sem secret em desenvolvimento: pula. Em produção sem secret: bloqueia.
    """
    secret = settings.recaptcha_secret_key()
    if not secret:
        if settings.is_production():
            logger.error("RECAPTCHA_SECRET_KEY ausente em produção.")
            return MSG_FALHA
        return None

    token_limpo = token_do_formulario(token)
    if not token_limpo:
        logger.warning("reCAPTCHA sem token no POST %s", request.url.path)
        return MSG_FALHA

    try:
        resp = requests.post(
            "https://www.google.com/recaptcha/api/siteverify",
            data={
                "secret": secret,
                "response": token_limpo,
                "remoteip": client_ip(request),
            },
            timeout=8,
        )
        dados = resp.json() if resp.ok else {}
    except Exception as exc:
        logger.warning("reCAPTCHA siteverify falhou: %s", exc)
        return MSG_FALHA

    if not dados.get("success"):
        logger.warning(
            "reCAPTCHA recusado path=%s codes=%s hostname=%s",
            request.url.path,
            dados.get("error-codes"),
            dados.get("hostname"),
        )
        return MSG_FALHA

    score = dados.get("score")
    if score is not None:
        try:
            if float(score) < settings.recaptcha_min_score():
                logger.warning("reCAPTCHA score baixo: %s", score)
                return MSG_FALHA
        except (TypeError, ValueError):
            return MSG_FALHA

    acao = str(dados.get("action") or "")
    if acao and acao_esperada and acao.lower() != acao_esperada.lower():
        logger.warning("reCAPTCHA action divergente: %s != %s", acao, acao_esperada)

    host_ok = str(dados.get("hostname") or "").lower().lstrip(".")
    esperado = urlparse(settings.site_url()).hostname or ""
    esperado = esperado.lower().lstrip("www.")
    if host_ok and esperado:
        host_base = host_ok.lstrip("www.")
        if host_base != esperado and host_ok != esperado:
            logger.warning("reCAPTCHA hostname inesperado: %s (esperado %s)", host_ok, esperado)

    return None
