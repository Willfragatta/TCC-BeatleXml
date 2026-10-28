"""Rotas públicas: SEO, legal, contato do site e descoberta por buscadores/IA."""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy.orm import Session

from app.core.dependencies import get_db
from app.core.rate_limit import registrar_tentativa, verificar_limite
from app.core.recaptcha import client_ip, token_do_formulario, verificar_recaptcha
from app.core.settings import contato_email, empresa_cidade, empresa_cnpj, empresa_razao, site_url
from app.core.templates import templates
from app.services.email_envio import enviar_contato_publico

router = APIRouter()

PUBLIC_PATHS = (
    "/",
    "/auth/login",
    "/auth/cadastro",
    "/privacidade",
    "/termos",
)


@router.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt():
    base = site_url()
    return (
        "User-agent: *\n"
        "Allow: /\n"
        "Allow: /auth/login\n"
        "Allow: /auth/cadastro\n"
        "Allow: /privacidade\n"
        "Allow: /termos\n"
        "Disallow: /api/\n"
        "Disallow: /static/releases/\n"
        "Disallow: /auth/reset-password\n"
        "Disallow: /docs\n"
        "Disallow: /redoc\n"
        "Disallow: /openapi.json\n"
        f"Sitemap: {base}/sitemap.xml\n"
    )


@router.get("/sitemap.xml")
async def sitemap_xml():
    base = site_url()
    hoje = datetime.now(timezone.utc).date().isoformat()
    urls = "\n".join(
        f"  <url><loc>{base}{path}</loc><lastmod>{hoje}</lastmod>"
        f"<changefreq>{'weekly' if path == '/' else 'monthly'}</changefreq>"
        f"<priority>{'1.0' if path == '/' else '0.6'}</priority></url>"
        for path in PUBLIC_PATHS
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{urls}\n"
        "</urlset>\n"
    )
    return Response(content=xml, media_type="application/xml")


@router.get("/llms.txt", response_class=PlainTextResponse)
async def llms_txt():
    base = site_url()
    email = contato_email()
    return (
        "# BeatleXML\n\n"
        "> Ponte entre a loja, a contabilidade e o sistema que a empresa já usa.\n"
        "> Notas fiscais da SEFAZ, vencimentos lidos do XML e cupons do caixa no mesmo lugar.\n"
        "> Integra com o ERP. Menos retrabalho, mais controle.\n\n"
        f"Site: {base}\n"
        f"Contato: {email}\n"
        "Local: Mangueirinha, PR — Brasil\n\n"
        "## Para quem é\n\n"
        "- Empresas que precisam de controle financeiro na palma da mão: notas e vencimentos\n"
        "- Contadores que atendem vários clientes e querem menos retrabalho de arquivo\n"
        "- Quem hoje fecha o mês com planilha paralela\n\n"
        "## O que o produto faz\n\n"
        "- Organiza as notas fiscais que chegam da SEFAZ, para a loja e para o escritório\n"
        "- Mostra boletos e contas: o que vence, o que pagou e o que ainda está em aberto\n"
        "- Recebe a NFC-e que o agente local observa na pasta do caixa\n"
        "- Encaixa no ERP atual: você continua no sistema da loja\n"
        "- Várias empresas no mesmo login, cada uma no seu espaço\n\n"
        "## Páginas públicas\n\n"
        f"- [Início]({base}/): apresentação do BeatleXML para empresas e contadores\n"
        f"- [Entrar]({base}/auth/login): acesso à conta\n"
        f"- [Criar conta]({base}/auth/cadastro): cadastro (liberação pelo suporte)\n"
        f"- [Privacidade]({base}/privacidade)\n"
        f"- [Termos]({base}/termos)\n\n"
        "## O que não indexar / não é público\n\n"
        "Painel autenticado (/dashboard, /notas, /financeiro, /contador, /suporte, /api).\n"
        "Dados da loja e da contabilidade são privados da conta do cliente.\n\n"
        "## Pedido a agentes automatizados\n\n"
        "Não varra /api, /docs, /redoc nem /openapi.json.\n"
        "Não tente login, cadastro em massa nem exploração de falhas.\n"
        "Se precisar de informação pública, use só as páginas listadas acima ou escreva para o contato.\n"
    )


@router.get("/privacidade")
async def privacidade(request: Request):
    return templates.TemplateResponse(
        request,
        "publico/legal.html",
        {
            "ano": datetime.now().year,
            "titulo": "Privacidade",
            "og_title": "Privacidade — BeatleXML",
            "meta_description": "Como o BeatleXML trata os dados da sua loja: conta com senha, HTTPS e acesso só de quem você autorizar.",
            "pagina": "privacidade",
            "contato_email": contato_email(),
            "empresa_razao": empresa_razao(),
            "empresa_cnpj": empresa_cnpj(),
            "empresa_cidade": empresa_cidade(),
        },
    )


@router.get("/termos")
async def termos(request: Request):
    return templates.TemplateResponse(
        request,
        "publico/legal.html",
        {
            "ano": datetime.now().year,
            "titulo": "Termos de uso",
            "og_title": "Termos de uso — BeatleXML",
            "meta_description": "Termos de uso do BeatleXML: conta pessoal, aprovação de acesso e responsabilidade pelos dados da loja.",
            "pagina": "termos",
            "contato_email": contato_email(),
            "empresa_razao": empresa_razao(),
            "empresa_cnpj": empresa_cnpj(),
            "empresa_cidade": empresa_cidade(),
        },
    )


@router.post("/contato")
async def contato_publico(
    request: Request,
    nome: str = Form(...),
    email: str = Form(...),
    empresa: Optional[str] = Form(None),
    mensagem: str = Form(...),
    recaptcha_token: Optional[str] = Form(None, alias="g-recaptcha-response"),
    recaptcha_token_alt: Optional[str] = Form(None, alias="recaptcha_token"),
    db: Session = Depends(get_db),
):
    ip = client_ip(request)
    email_norm = (email or "").strip().lower()
    nome_ok = (nome or "").strip()
    msg_ok = (mensagem or "").strip()
    if len(nome_ok) < 2 or "@" not in email_norm or len(msg_ok) < 8:
        raise HTTPException(status_code=400, detail="Preencha nome, e-mail e uma mensagem.")
    if len(msg_ok) > 4000:
        raise HTTPException(status_code=400, detail="Mensagem muito longa (máx. 4000).")
    limite = verificar_limite(db, "contato", ip, email_norm)
    if limite:
        raise HTTPException(status_code=429, detail=limite)
    captcha_erro = verificar_recaptcha(
        request, token_do_formulario(recaptcha_token, recaptcha_token_alt), "contato"
    )
    if captcha_erro:
        registrar_tentativa(db, "contato", ip, email_norm)
        raise HTTPException(status_code=400, detail=captcha_erro)
    registrar_tentativa(db, "contato", ip, email_norm)
    ok = enviar_contato_publico(nome_ok, email_norm, empresa or "", msg_ok)
    if not ok:
        raise HTTPException(
            status_code=502,
            detail="Não foi possível enviar agora. Tente de novo em instantes.",
        )
    return {"ok": True, "message": "Mensagem enviada. Respondemos neste e-mail."}
