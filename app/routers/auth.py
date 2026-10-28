import os
import hashlib
import logging
import secrets
from urllib.parse import quote
from fastapi import APIRouter, Depends, HTTPException, status, Request, Form
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr
from app.models.database import Usuario, EmailToken, AuthOtp
from app.models.password_reset import PasswordReset
from app.core.auth import (
    criar_token,
    criar_token_pendente_2fa,
    hash_senha,
    verificar_senha,
    verificar_token_2fa,
)
from app.core.dependencies import get_db, get_usuario_atual
from app.core.templates import templates
from app.core.settings import is_production, contato_email, site_url
from app.core.recaptcha import verificar_recaptcha, client_ip, token_do_formulario
from app.core.rate_limit import verificar_limite, registrar_tentativa, limpar_sucesso
from app.services.email_envio import enviar_confirmacao, enviar_reset_senha, enviar_codigo_2fa
from datetime import datetime, timedelta
from typing import Optional

router = APIRouter()
logger = logging.getLogger(__name__)

MSG_CREDENCIAIS = "Email ou senha incorretos"
MSG_STATUS = {
    "pendente": "Sua conta aguarda aprovação do suporte.",
    "suspenso": "Sua conta está bloqueada. Fale com o suporte.",
    "cancelado": "Esta conta foi encerrada.",
}


def _cookie_secure(request: Request) -> bool:
    if os.getenv("HTTPS", "").lower() in ("1", "true", "on"):
        return True
    return request.headers.get("X-Forwarded-Proto") == "https"


def _redirect_erro(path: str, mensagem: str, html: bool):
    if html:
        return RedirectResponse(
            url=f"{path}?erro={quote(mensagem)}",
            status_code=302,
        )
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=mensagem)


def _hash_otp(codigo: str) -> str:
    from app.core.auth import SECRET_KEY
    return hashlib.sha256(f"{SECRET_KEY}:{codigo}".encode("utf-8")).hexdigest()


def _criar_token_confirmacao(db: Session, usuario: Usuario) -> str:
    db.query(EmailToken).filter_by(
        usuario_id=usuario.id, tipo="confirmar", used=False
    ).update({"used": True})
    token = secrets.token_urlsafe(32)
    db.add(EmailToken(
        usuario_id=usuario.id,
        token=token,
        tipo="confirmar",
        expires_at=datetime.now() + timedelta(hours=24),
        used=False,
    ))
    db.commit()
    return token


def _enviar_confirmacao_usuario(db: Session, usuario: Usuario) -> bool:
    token = _criar_token_confirmacao(db, usuario)
    link = f"{site_url()}/auth/confirmar-email?token={token}"
    return enviar_confirmacao(
        usuario.email,
        getattr(usuario, "nome_completo", None) or usuario.email,
        link,
    )


def _destino_pos_login(db: Session, usuario: Usuario) -> str:
    if getattr(usuario, "role", None) == "suporte" or getattr(usuario, "is_admin", False):
        return "/suporte"
    from app.core.dependencies import ids_empresas_acessiveis
    if ids_empresas_acessiveis(db, usuario):
        return "/dashboard"
    return "/empresas"


def _resposta_login_ok(request: Request, db: Session, usuario: Usuario, html: bool, login_data):
    limpar_sucesso(db, "login", email=(usuario.email or "").strip().lower())
    usuario.last_login = datetime.now()
    db.commit()
    token = criar_token(usuario.id)
    if request.headers.get("content-type", "").startswith("application/json") or login_data:
        return {
            "access_token": token,
            "token_type": "bearer",
            "usuario": {
                "id": usuario.id,
                "email": usuario.email,
                "nome": getattr(usuario, "nome_completo", None) or usuario.email,
                "role": getattr(usuario, "role", "cliente"),
            },
        }
    response = RedirectResponse(url=_destino_pos_login(db, usuario), status_code=302)
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        secure=_cookie_secure(request),
        samesite="lax",
        max_age=30 * 24 * 60 * 60,
    )
    response.delete_cookie(key="pending_2fa")
    return response


def _iniciar_2fa(request: Request, db: Session, usuario: Usuario, html: bool, ip: str):
    email_norm = (usuario.email or "").strip().lower()
    limite = verificar_limite(db, "otp", ip, email_norm)
    if limite:
        if html:
            return _redirect_erro("/auth/login", limite, True)
        raise HTTPException(status_code=429, detail=limite)

    db.query(AuthOtp).filter_by(usuario_id=usuario.id, used=False).update({"used": True})
    codigo = f"{secrets.randbelow(1_000_000):06d}"
    db.add(AuthOtp(
        usuario_id=usuario.id,
        codigo_hash=_hash_otp(codigo),
        expires_at=datetime.now() + timedelta(minutes=10),
        tentativas=0,
        used=False,
    ))
    db.commit()
    ok = enviar_codigo_2fa(
        usuario.email,
        getattr(usuario, "nome_completo", None) or usuario.email,
        codigo,
    )
    if not is_production():
        logger.warning("OTP (dev) usuario=%s codigo=%s", usuario.id, codigo)
    if not ok:
        logger.warning("2FA: e-mail não enviado para %s", usuario.email)
    pending = criar_token_pendente_2fa(usuario.id)
    registrar_tentativa(db, "otp", ip, email_norm)
    if html:
        dest = "/auth/verificar-2fa"
        if not ok:
            dest += "?erro=" + quote("Não foi possível enviar o código. Tente reenviar.")
        resp = RedirectResponse(url=dest, status_code=302)
        resp.set_cookie(
            key="pending_2fa",
            value=pending,
            httponly=True,
            secure=_cookie_secure(request),
            samesite="lax",
            max_age=600,
        )
        return resp
    resp = JSONResponse({
        "requires_2fa": True,
        "message": "Enviamos um código para o seu e-mail.",
    })
    resp.set_cookie(
        key="pending_2fa",
        value=pending,
        httponly=True,
        secure=_cookie_secure(request),
        samesite="lax",
        max_age=600,
    )
    return resp


def _usuario_pendente_2fa(request: Request, db: Session) -> Optional[Usuario]:
    raw = request.cookies.get("pending_2fa")
    if not raw:
        return None
    uid = verificar_token_2fa(raw)
    if not uid:
        return None
    return db.query(Usuario).filter_by(id=uid).first()


class LoginRequest(BaseModel):
    email: str
    senha: str
    recaptcha_token: Optional[str] = None


class CadastroRequest(BaseModel):
    nome_completo: str
    email: EmailStr
    senha: str
    telefone: Optional[str] = None
    tipo: Optional[str] = None
    recaptcha_token: Optional[str] = None


@router.post("/login")
async def login(
    request: Request,
    email: Optional[str] = Form(None),
    senha: Optional[str] = Form(None),
    recaptcha_token: Optional[str] = Form(None, alias="g-recaptcha-response"),
    recaptcha_token_alt: Optional[str] = Form(None, alias="recaptcha_token"),
    login_data: Optional[LoginRequest] = None,
    db: Session = Depends(get_db),
):
    html = bool(email and senha)
    if email and senha:
        email_login = email
        senha_login = senha
        token_captcha = token_do_formulario(recaptcha_token, recaptcha_token_alt)
    elif login_data:
        email_login = login_data.email
        senha_login = login_data.senha
        token_captcha = login_data.recaptcha_token
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email e senha são obrigatórios",
        )

    ip = client_ip(request)
    email_norm = (email_login or "").strip().lower()

    limite = verificar_limite(db, "login", ip, email_norm)
    if limite:
        if html:
            return _redirect_erro("/auth/login", limite, True)
        raise HTTPException(status_code=429, detail=limite)

    captcha_erro = verificar_recaptcha(request, token_captcha, "login")
    if captcha_erro:
        registrar_tentativa(db, "login", ip, email_norm)
        if html:
            return _redirect_erro("/auth/login", captcha_erro, True)
        raise HTTPException(status_code=400, detail=captcha_erro)

    usuario = db.query(Usuario).filter_by(email=email_login).first()
    if not usuario or not verificar_senha(senha_login, usuario.senha_hash):
        registrar_tentativa(db, "login", ip, email_norm)
        if html:
            return RedirectResponse(url="/auth/login?erro=" + quote(MSG_CREDENCIAIS), status_code=302)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=MSG_CREDENCIAIS)

    status_u = getattr(usuario, "status", None) or "ativo"
    if status_u != "ativo":
        msg = MSG_STATUS.get(status_u, "Acesso não liberado.")
        if status_u == "pendente":
            if html:
                return RedirectResponse(url="/auth/aguardando-aprovacao", status_code=302)
            raise HTTPException(status_code=403, detail=msg)
        if html:
            return RedirectResponse(url=f"/auth/login?erro={quote(msg)}", status_code=302)
        raise HTTPException(status_code=403, detail=msg)

    if getattr(usuario, "dois_fatores_email", False):
        return _iniciar_2fa(request, db, usuario, html, ip)

    return _resposta_login_ok(request, db, usuario, html, login_data)


@router.post("/cadastro")
async def cadastro(
    request: Request,
    nome_completo: Optional[str] = Form(None),
    email: Optional[str] = Form(None),
    telefone: Optional[str] = Form(None),
    senha: Optional[str] = Form(None),
    tipo: Optional[str] = Form(None),
    recaptcha_token: Optional[str] = Form(None, alias="g-recaptcha-response"),
    recaptcha_token_alt: Optional[str] = Form(None, alias="recaptcha_token"),
    cadastro_data: Optional[CadastroRequest] = None,
    db: Session = Depends(get_db),
):
    msg_gen = "Dados inválidos. Verifique os campos."
    html = bool(nome_completo and email and senha)
    try:
        if nome_completo and email and senha:
            nome = nome_completo
            email_cad = email
            telefone_cad = telefone
            senha_cad = senha
            tipo_cad = tipo
            token_captcha = token_do_formulario(recaptcha_token, recaptcha_token_alt)
        elif cadastro_data:
            nome = cadastro_data.nome_completo
            email_cad = cadastro_data.email
            telefone_cad = cadastro_data.telefone
            senha_cad = cadastro_data.senha
            tipo_cad = cadastro_data.tipo
            token_captcha = cadastro_data.recaptcha_token
        else:
            if nome_completo and email:
                return RedirectResponse(url="/auth/cadastro?erro=Dados+incompletos", status_code=302)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Dados incompletos")

        ip = client_ip(request)
        email_norm = (email_cad or "").strip().lower()
        limite = verificar_limite(db, "cadastro", ip, email_norm)
        if limite:
            if html:
                return _redirect_erro("/auth/cadastro", limite, True)
            raise HTTPException(status_code=429, detail=limite)

        captcha_erro = verificar_recaptcha(request, token_captcha, "cadastro")
        if captcha_erro:
            registrar_tentativa(db, "cadastro", ip, email_norm)
            if html:
                return _redirect_erro("/auth/cadastro", captcha_erro, True)
            raise HTTPException(status_code=400, detail=captcha_erro)

        if db.query(Usuario).filter_by(email=email_cad).first():
            registrar_tentativa(db, "cadastro", ip, email_norm)
            if html:
                return RedirectResponse(url="/auth/cadastro?erro=Email+já+cadastrado", status_code=302)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email já cadastrado")

        novo_usuario = Usuario(
            email=email_cad,
            senha_hash=hash_senha(senha_cad),
            nome_completo=nome,
            telefone=telefone_cad,
            status="pendente",
            role="contador" if (tipo_cad or "").strip().lower() == "contador" else "cliente",
            created_at=datetime.now(),
            dois_fatores_email=False,
        )
        db.add(novo_usuario)
        db.commit()
        db.refresh(novo_usuario)
        registrar_tentativa(db, "cadastro", ip, email_norm)
        _enviar_confirmacao_usuario(db, novo_usuario)

        if request.headers.get("content-type", "").startswith("application/json") or cadastro_data:
            return {
                "ok": True,
                "status": "pendente",
                "message": "Cadastro recebido. Confirme o e-mail; o suporte libera o acesso em breve.",
            }

        return RedirectResponse(url="/auth/aguardando-aprovacao", status_code=302)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Erro no cadastro: %s", e)
        if request.headers.get("content-type", "").startswith("application/json"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg_gen)
        return RedirectResponse(url=f"/auth/cadastro?erro={quote(msg_gen)}", status_code=302)


@router.get("/me")
async def me_usuario(usuario: Usuario = Depends(get_usuario_atual)):
    return {
        "id": usuario.id,
        "email": usuario.email,
        "nome": usuario.nome_completo or usuario.email,
        "telefone": usuario.telefone,
        "status": usuario.status,
        "role": getattr(usuario, "role", "cliente"),
        "email_confirmado": bool(getattr(usuario, "email_confirmado_em", None)),
        "dois_fatores_email": bool(getattr(usuario, "dois_fatores_email", False)),
    }


@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/auth/login", status_code=302)
    response.delete_cookie(key="access_token")
    response.delete_cookie(key="pending_2fa")
    return response


@router.get("/login")
async def pagina_login(request: Request):
    return templates.TemplateResponse(request, "auth/login.html")


@router.get("/cadastro")
async def pagina_cadastro(request: Request):
    return templates.TemplateResponse(request, "auth/cadastro.html")


@router.get("/aguardando-aprovacao")
async def pagina_aguardando(request: Request):
    return templates.TemplateResponse(
        request,
        "auth/aguardando.html",
        {"contato_email": contato_email()},
    )


@router.get("/confirmar-email")
async def confirmar_email(request: Request, token: str = "", db: Session = Depends(get_db)):
    tok = (token or "").strip()
    registro = db.query(EmailToken).filter_by(token=tok, tipo="confirmar", used=False).first()
    ok = False
    if registro and registro.expires_at and registro.expires_at > datetime.now():
        usuario = db.query(Usuario).filter_by(id=registro.usuario_id).first()
        if usuario:
            usuario.email_confirmado_em = datetime.now()
            registro.used = True
            db.commit()
            ok = True
    return templates.TemplateResponse(
        request,
        "auth/confirmar_email.html",
        {"ok": ok, "contato_email": contato_email()},
    )


@router.post("/reenviar-confirmacao")
async def reenviar_confirmacao(
    request: Request,
    email: str = Form(...),
    recaptcha_token: Optional[str] = Form(None, alias="g-recaptcha-response"),
    recaptcha_token_alt: Optional[str] = Form(None, alias="recaptcha_token"),
    db: Session = Depends(get_db),
):
    ip = client_ip(request)
    email_norm = (email or "").strip().lower()
    msg_ok = "Se o e-mail existir e ainda não estiver confirmado, enviaremos um novo link."
    limite = verificar_limite(db, "confirmar", ip, email_norm)
    if limite:
        raise HTTPException(status_code=429, detail=limite)
    captcha_erro = verificar_recaptcha(
        request, token_do_formulario(recaptcha_token, recaptcha_token_alt), "cadastro"
    )
    if captcha_erro:
        registrar_tentativa(db, "confirmar", ip, email_norm)
        raise HTTPException(status_code=400, detail=captcha_erro)
    registrar_tentativa(db, "confirmar", ip, email_norm)
    usuario = db.query(Usuario).filter_by(email=email).first()
    if usuario and not getattr(usuario, "email_confirmado_em", None):
        _enviar_confirmacao_usuario(db, usuario)
    return {"message": msg_ok}


@router.get("/verificar-2fa")
async def pagina_verificar_2fa(request: Request, db: Session = Depends(get_db)):
    if not _usuario_pendente_2fa(request, db):
        return RedirectResponse(url="/auth/login", status_code=302)
    return templates.TemplateResponse(request, "auth/verificar_2fa.html")


@router.post("/verificar-2fa")
async def verificar_2fa(
    request: Request,
    codigo: str = Form(...),
    db: Session = Depends(get_db),
):
    ip = client_ip(request)
    usuario = _usuario_pendente_2fa(request, db)
    if not usuario:
        return RedirectResponse(url="/auth/login?erro=" + quote("Sessão expirada. Entre de novo."), status_code=302)
    email_norm = (usuario.email or "").strip().lower()
    limite = verificar_limite(db, "otp", ip, email_norm)
    if limite:
        return _redirect_erro("/auth/verificar-2fa", limite, True)
    otp = (
        db.query(AuthOtp)
        .filter_by(usuario_id=usuario.id, used=False)
        .order_by(AuthOtp.id.desc())
        .first()
    )
    digits = "".join(ch for ch in (codigo or "") if ch.isdigit())
    if not otp or otp.expires_at < datetime.now():
        registrar_tentativa(db, "otp", ip, email_norm)
        return _redirect_erro("/auth/verificar-2fa", "Código expirado. Peça um novo.", True)
    otp.tentativas = int(otp.tentativas or 0) + 1
    if otp.tentativas > 5:
        otp.used = True
        db.commit()
        resp = RedirectResponse(url="/auth/login?erro=" + quote("Muitas tentativas. Entre de novo."), status_code=302)
        resp.delete_cookie(key="pending_2fa")
        return resp
    if _hash_otp(digits) != otp.codigo_hash:
        db.commit()
        registrar_tentativa(db, "otp", ip, email_norm)
        return _redirect_erro("/auth/verificar-2fa", "Código incorreto.", True)
    otp.used = True
    db.commit()
    return _resposta_login_ok(request, db, usuario, True, None)


@router.post("/reenviar-2fa")
async def reenviar_2fa(request: Request, db: Session = Depends(get_db)):
    ip = client_ip(request)
    usuario = _usuario_pendente_2fa(request, db)
    if not usuario:
        raise HTTPException(status_code=401, detail="Sessão expirada. Entre de novo.")
    return _iniciar_2fa(request, db, usuario, True, ip)


@router.post("/esqueci-senha")
async def solicitar_reset_senha(
    request: Request,
    email: str = Form(...),
    recaptcha_token: Optional[str] = Form(None, alias="g-recaptcha-response"),
    recaptcha_token_alt: Optional[str] = Form(None, alias="recaptcha_token"),
    db: Session = Depends(get_db),
):
    ip = client_ip(request)
    email_norm = (email or "").strip().lower()
    limite = verificar_limite(db, "reset", ip, email_norm)
    if limite:
        raise HTTPException(status_code=429, detail=limite)

    captcha_erro = verificar_recaptcha(
        request, token_do_formulario(recaptcha_token, recaptcha_token_alt), "reset"
    )
    if captcha_erro:
        registrar_tentativa(db, "reset", ip, email_norm)
        raise HTTPException(status_code=400, detail=captcha_erro)

    registrar_tentativa(db, "reset", ip, email_norm)
    usuario = db.query(Usuario).filter_by(email=email).first()
    msg_ok = "Se o email existir, um link de recuperação será enviado."
    if not usuario:
        return {"message": msg_ok}

    token = secrets.token_urlsafe(32)
    expires_at = datetime.now() + timedelta(hours=1)
    db.query(PasswordReset).filter_by(usuario_id=usuario.id, used=False).update({"used": True})
    db.add(PasswordReset(usuario_id=usuario.id, token=token, expires_at=expires_at))
    db.commit()

    link = f"{site_url()}/auth/reset-password?token={token}"
    enviar_reset_senha(
        usuario.email,
        getattr(usuario, "nome_completo", None) or usuario.email,
        link,
    )

    payload = {"message": msg_ok}
    if not is_production():
        payload["reset_url"] = f"/auth/reset-password?token={token}"
    return payload


@router.get("/esqueci-senha")
async def pagina_esqueci_senha(request: Request):
    return templates.TemplateResponse(request, "auth/esqueci_senha.html")


@router.get("/reset-password")
async def pagina_reset_senha(request: Request, token: str = None):
    return templates.TemplateResponse(request, "auth/reset_password.html", {"token": token})


@router.post("/reset-password")
async def resetar_senha(
    request: Request,
    token: str = Form(...),
    nova_senha: str = Form(...),
    recaptcha_token: Optional[str] = Form(None, alias="g-recaptcha-response"),
    recaptcha_token_alt: Optional[str] = Form(None, alias="recaptcha_token"),
    db: Session = Depends(get_db),
):
    ip = client_ip(request)
    limite = verificar_limite(db, "reset", ip, None)
    if limite:
        raise HTTPException(status_code=429, detail=limite)
    captcha_erro = verificar_recaptcha(
        request, token_do_formulario(recaptcha_token, recaptcha_token_alt), "reset"
    )
    if captcha_erro:
        registrar_tentativa(db, "reset", ip, None)
        raise HTTPException(status_code=400, detail=captcha_erro)

    reset = db.query(PasswordReset).filter_by(token=token, used=False).first()
    if not reset or not reset.is_valid():
        registrar_tentativa(db, "reset", ip, None)
        raise HTTPException(status_code=400, detail="Token inválido ou expirado")

    usuario = db.query(Usuario).filter_by(id=reset.usuario_id).first()
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")

    usuario.senha_hash = hash_senha(nova_senha)
    reset.used = True
    db.commit()
    return {"message": "Senha alterada com sucesso"}
