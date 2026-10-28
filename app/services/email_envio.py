"""Envio de e-mail da demonstração (Mailpit ou SMTP configurado). Falha não derruba a request."""
from __future__ import annotations

import html as html_lib
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, parseaddr
from typing import Optional

from app.core import settings
from app.core.templates import templates

logger = logging.getLogger("beatle_xml")


def _html_para_texto(html: str) -> str:
    texto = html.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    out = []
    dentro = False
    for ch in texto:
        if ch == "<":
            dentro = True
            continue
        if ch == ">":
            dentro = False
            continue
        if not dentro:
            out.append(ch)
    return "".join(out).strip()


def renderizar_mensagem(
    titulo: str,
    corpo_html: str,
    assunto: str,
    botao_url: str = "",
    botao_texto: str = "",
) -> str:
    return templates.env.get_template("email/mensagem.html").render(
        titulo=titulo,
        corpo_html=corpo_html,
        assunto=assunto,
        botao_url=botao_url,
        botao_texto=botao_texto,
        from_name=settings.smtp_from_name(),
        contato=settings.contato_email(),
        site_url=settings.site_url(),
    )


def enviar_email(
    para: str,
    assunto: str,
    html: str,
    texto: Optional[str] = None,
    reply_to: Optional[str] = None,
) -> bool:
    if not settings.smtp_configurado():
        logger.warning("SMTP não configurado; e-mail não enviado para %s (%s)", para, assunto)
        return False
    destino = (para or "").strip()
    if not destino or "@" not in destino:
        logger.warning("Destino de e-mail inválido: %r", para)
        return False

    from_addr = settings.smtp_from() or settings.smtp_user() or "noreply@localhost"
    from_name = settings.smtp_from_name()
    user = settings.smtp_user()
    reply = (reply_to or settings.mail_suporte() or "").strip()

    msg = MIMEMultipart("alternative")
    msg["Subject"] = assunto
    msg["From"] = formataddr((from_name, from_addr))
    msg["To"] = destino
    if reply and parseaddr(reply)[1]:
        msg["Reply-To"] = reply

    corpo_txt = texto or _html_para_texto(html)
    msg.attach(MIMEText(corpo_txt, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    host = settings.smtp_host()
    port = settings.smtp_port()
    timeout = settings.smtp_timeout()
    password = settings.smtp_password()

    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=timeout) as smtp:
                if user and password:
                    smtp.login(user, password)
                smtp.sendmail(from_addr, [destino], msg.as_string())
        elif port == 1025 or (os.getenv("SMTP_STARTTLS") or "1").lower() in ("0", "false", "no"):
            with smtplib.SMTP(host, port, timeout=timeout) as smtp:
                if user and password:
                    smtp.login(user, password)
                smtp.sendmail(from_addr, [destino], msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=timeout) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.ehlo()
                if user and password:
                    smtp.login(user, password)
                smtp.sendmail(from_addr, [destino], msg.as_string())
        logger.info("E-mail enviado para %s (%s)", destino, assunto)
        return True
    except Exception:
        if user and from_addr.lower() != user.lower():
            try:
                msg.replace_header("From", formataddr((from_name, user)))
                if port == 465:
                    with smtplib.SMTP_SSL(host, port, timeout=timeout) as smtp:
                        smtp.login(user, password)
                        smtp.sendmail(user, [destino], msg.as_string())
                else:
                    with smtplib.SMTP(host, port, timeout=timeout) as smtp:
                        smtp.ehlo()
                        smtp.starttls()
                        smtp.ehlo()
                        smtp.login(user, password)
                        smtp.sendmail(user, [destino], msg.as_string())
                logger.info("E-mail enviado (From=caixa) para %s (%s)", destino, assunto)
                return True
            except Exception:
                logger.exception("Falha SMTP (retry From=caixa) para %s", destino)
                return False
        logger.exception("Falha SMTP para %s", destino)
        return False


def enviar_confirmacao(para: str, nome: str, link: str) -> bool:
    assunto = "Confirme seu e-mail — BeatleXML"
    nome_ok = html_lib.escape((nome or "olá").strip() or "olá")
    corpo = (
        f"<p>Olá, {nome_ok}.</p>"
        "<p>Recebemos o seu cadastro. Confirme este e-mail para o suporte saber que o endereço é seu.</p>"
        "<p>Depois disso, ainda é preciso a liberação do suporte para entrar no painel.</p>"
        "<p>O link vale 24 horas.</p>"
    )
    html = renderizar_mensagem("Confirme seu e-mail", corpo, assunto, link, "Confirmar e-mail")
    return enviar_email(para, assunto, html)


def enviar_reset_senha(para: str, nome: str, link: str) -> bool:
    assunto = "Redefinir senha — BeatleXML"
    nome_ok = html_lib.escape((nome or "olá").strip() or "olá")
    corpo = (
        f"<p>Olá, {nome_ok}.</p>"
        "<p>Alguém pediu para redefinir a senha desta conta. Se foi você, use o botão abaixo. "
        "O link vale 1 hora e só pode ser usado uma vez.</p>"
        "<p>Se não foi você, ignore este e-mail. A senha atual continua valendo.</p>"
    )
    html = renderizar_mensagem("Redefinir senha", corpo, assunto, link, "Escolher nova senha")
    return enviar_email(para, assunto, html)


def enviar_codigo_2fa(para: str, nome: str, codigo: str) -> bool:
    assunto = "Código de acesso — BeatleXML"
    nome_ok = html_lib.escape((nome or "olá").strip() or "olá")
    codigo_ok = html_lib.escape("".join(ch for ch in (codigo or "") if ch.isdigit())[:6])
    corpo = (
        f"<p>Olá, {nome_ok}.</p>"
        "<p>Seu código para entrar no BeatleXML:</p>"
        f'<p style="font-size:28px;letter-spacing:6px;font-weight:bold;color:#0f172a;">{codigo_ok}</p>'
        "<p>Vale 10 minutos. Se você não tentou entrar, ignore este e-mail e altere a senha.</p>"
    )
    html = renderizar_mensagem("Código de verificação", corpo, assunto)
    return enviar_email(para, assunto, html)


def enviar_contato_publico(nome: str, email: str, empresa: str, mensagem: str) -> bool:
    destino = settings.mail_suporte() or settings.contato_email()
    nome_limpo = (nome or "").strip() or "—"
    email_limpo = (email or "").strip()
    empresa_limpa = (empresa or "").strip() or "—"
    msg_limpa = (mensagem or "").strip()
    assunto = f"[BeatleXML] Contato do site: {nome_limpo}"[:120]
    corpo = (
        f"<p><strong>Nome:</strong> {html_lib.escape(nome_limpo)}</p>"
        f"<p><strong>E-mail:</strong> {html_lib.escape(email_limpo)}</p>"
        f"<p><strong>Empresa:</strong> {html_lib.escape(empresa_limpa)}</p>"
        f"<p><strong>Mensagem:</strong></p><p>{html_lib.escape(msg_limpa).replace(chr(10), '<br>')}</p>"
    )
    html = renderizar_mensagem("Mensagem do site", corpo, assunto)
    return enviar_email(destino, assunto, html, reply_to=email_limpo or None)
