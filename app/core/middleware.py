import os
from fastapi import Request, status
from fastapi.responses import RedirectResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp
from app.core.auth import verificar_token
from app.core.dependencies import PAPEIS_ESCRITA, papel_na_empresa, usuario_acessa_empresa, paginas_do_usuario
from app.core.paginas import acesso_pagina_ok, primeira_pagina, path_exige_pagina, usuario_eh_staff
from app.core.settings import is_production, site_url
from app.models.database import SessionLocal, Usuario, Empresa

# Mutações JSON do browser devem enviar este header (proteção CSRF leve).
CSRF_HEADER = "X-Requested-With"
CSRF_VALUE = "BeatleXML"
CSRF_METODOS = {"POST", "PUT", "PATCH", "DELETE"}
# Uploader e login não passam pelo fetch do painel.
CSRF_EXCLUIR_PREFIXOS = (
    "/api/uploader",
    "/api/contador",
    "/auth/login",
    "/auth/cadastro",
    "/auth/esqueci-senha",
    "/auth/reset-password",
    "/auth/reenviar-confirmacao",
    "/auth/verificar-2fa",
    "/auth/reenviar-2fa",
    "/contato",
)
# Rotas de API em que perfil "leitura" não pode mutar.
ESCRITA_PREFIXOS = (
    "/api/financeiro",
    "/api/empresas",
    "/api/notas",
    "/api/etiquetas",
    "/config/",
)

STATUS_BLOQUEADOS = {"pendente", "suspenso", "cancelado"}


def _csp() -> str:
    politica = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com "
        "https://cdn.tailwindcss.com https://cdn.jsdelivr.net "
        "https://www.google.com https://www.gstatic.com https://www.googletagmanager.com "
        "https://www.recaptcha.net https://www.gstatic.com/recaptcha/ "
        "https://www.google.com/recaptcha/; "
        "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://cdn.tailwindcss.com "
        "https://www.gstatic.com; "
        "img-src 'self' data: https://www.google.com https://www.gstatic.com "
        "https://www.googletagmanager.com https://www.google-analytics.com; "
        "font-src 'self' https://cdnjs.cloudflare.com; "
        "connect-src 'self' https://www.google.com https://www.gstatic.com "
        "https://www.google-analytics.com https://region1.google-analytics.com "
        "https://www.googletagmanager.com https://www.google.com/recaptcha/ "
        "https://www.recaptcha.net https://recaptcha.google.com "
        "https://storage.googleapis.com; "
        "media-src 'self' https://storage.googleapis.com; "
        "frame-src https://www.google.com https://www.gstatic.com "
        "https://www.google.com/recaptcha/ https://www.recaptcha.net "
        "https://recaptcha.google.com; "
        "worker-src 'self' blob:; "
        "frame-ancestors 'none'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self';"
    )
    if is_production():
        politica += " upgrade-insecure-requests;"
    return politica


class CanonicalHostMiddleware(BaseHTTPMiddleware):
    """Em produção, manda beatlexml.com.br para www (chave reCAPTCHA e cookies)."""

    async def dispatch(self, request: Request, call_next):
        if is_production():
            host = (
                request.headers.get("x-forwarded-host")
                or request.headers.get("host")
                or ""
            ).split(",")[0].split(":")[0].strip().lower()
            if host == "beatlexml.com.br":
                destino = site_url() + request.url.path
                if request.url.query:
                    destino += "?" + str(request.url.query)
                return RedirectResponse(url=destino, status_code=301)
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Injeta headers de segurança em todas as respostas (OWASP)."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = _csp()
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        )
        is_https = (
            os.getenv("HTTPS", "").lower() in ("1", "true", "on")
            or request.headers.get("X-Forwarded-Proto") == "https"
        )
        if is_https:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        return response


def _exige_csrf(request: Request) -> bool:
    if request.method not in CSRF_METODOS:
        return False
    path = request.url.path
    if any(path.startswith(p) for p in CSRF_EXCLUIR_PREFIXOS):
        return False
    if path.startswith("/api/") or path.startswith("/config/"):
        return True
    if path.startswith("/auth/") and path not in (
        "/auth/login", "/auth/cadastro", "/auth/logout",
        "/auth/esqueci-senha", "/auth/reset-password",
        "/auth/reenviar-confirmacao", "/auth/confirmar-email",
        "/auth/verificar-2fa", "/auth/reenviar-2fa",
    ):
        return True
    return False


def _rotas_publicas_exatas():
    rotas = {
        "/",
        "/robots.txt",
        "/sitemap.xml",
        "/llms.txt",
        "/privacidade",
        "/termos",
        "/favicon.ico",
        "/contato",
    }
    if not is_production():
        rotas.update({"/openapi.json", "/redoc"})
    return rotas


def _rotas_publicas_prefixo():
    rotas = [
        "/static",
        "/auth/login",
        "/auth/cadastro",
        "/auth/logout",
        "/auth/esqueci-senha",
        "/auth/reset-password",
        "/auth/aguardando-aprovacao",
        "/auth/confirmar-email",
        "/auth/reenviar-confirmacao",
        "/auth/verificar-2fa",
        "/auth/reenviar-2fa",
        "/api/uploader/login",
        "/api/contador/login",
    ]
    if not is_production():
        rotas.append("/docs")
    return rotas


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware para verificar autenticação em rotas protegidas."""

    def __init__(self, app: ASGIApp):
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in _rotas_publicas_exatas() or any(
            path.startswith(rota) for rota in _rotas_publicas_prefixo()
        ):
            return await call_next(request)

        if _exige_csrf(request):
            if request.headers.get(CSRF_HEADER) != CSRF_VALUE:
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"detail": "Requisição rejeitada (CSRF). Atualize a página."},
                )

        token = request.cookies.get("access_token")
        if not token:
            auth_header = request.headers.get("Authorization")
            if auth_header and auth_header.startswith("Bearer "):
                token = auth_header.split(" ")[1]

        if not token:
            return self._sem_auth(path)

        usuario_id = verificar_token(token)
        if not usuario_id:
            return self._sem_auth(path)

        db = SessionLocal()
        try:
            usuario = db.query(Usuario).filter_by(id=usuario_id).first()
            if not usuario:
                return self._sem_auth(path)

            status_u = getattr(usuario, "status", None) or "ativo"
            if status_u in STATUS_BLOQUEADOS:
                return self._status_bloqueado(path, status_u)

            request.state.usuario = usuario
            request.state.usuario_id = usuario_id

            empresa = self._empresa_contexto(db, request, usuario)
            paginas = paginas_do_usuario(db, usuario, empresa)
            request.state.paginas_permitidas = list(paginas) if paginas is not None else None

            # Sem empresa: forcar onboarding em /empresas (evita dashboard com JSON cru)
            if (
                empresa is None
                and not usuario_eh_staff(usuario)
                and not path.startswith("/api/")
                and not path.startswith(("/empresas", "/auth/", "/static"))
                and path_exige_pagina(path)
            ):
                return RedirectResponse(url="/empresas", status_code=302)

            if not acesso_pagina_ok(paginas, path):
                if path.startswith("/api/"):
                    return JSONResponse(
                        status_code=status.HTTP_403_FORBIDDEN,
                        content={"detail": "Você não tem acesso a esta área."},
                    )
                # Sem empresa: nunca mandar para /dashboard
                if empresa is None and not usuario_eh_staff(usuario):
                    return RedirectResponse(url="/empresas", status_code=302)
                destino = primeira_pagina(paginas)
                return RedirectResponse(url=destino, status_code=302)

            if (
                request.method in CSRF_METODOS
                and any(path.startswith(p) for p in ESCRITA_PREFIXOS)
                and not usuario_eh_staff(usuario)
                and empresa is not None
            ):
                papel = papel_na_empresa(db, usuario, empresa)
                if papel not in PAPEIS_ESCRITA:
                    return JSONResponse(
                        status_code=status.HTTP_403_FORBIDDEN,
                        content={"detail": "Seu perfil é somente leitura nesta empresa."},
                    )
        finally:
            db.close()

        return await call_next(request)

    @staticmethod
    def _empresa_contexto(db, request: Request, usuario: Usuario):
        cookie_emp = request.cookies.get("empresa_ativa")
        if cookie_emp:
            try:
                eid = int(cookie_emp)
                if usuario_acessa_empresa(db, usuario, eid):
                    return db.query(Empresa).filter_by(id=eid).first()
            except Exception:
                pass
        from app.core.dependencies import ids_empresas_acessiveis
        acessiveis = ids_empresas_acessiveis(db, usuario)
        if acessiveis:
            return db.query(Empresa).filter(Empresa.id.in_(acessiveis)).first()
        return None

    @staticmethod
    def _sem_auth(path: str):
        if path.startswith("/api/"):
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Token de autenticação necessário"},
            )
        return RedirectResponse(url="/auth/login", status_code=302)

    @staticmethod
    def _status_bloqueado(path: str, status_u: str):
        if path.startswith("/api/"):
            detalhe = {
                "pendente": "Sua conta aguarda aprovação do suporte.",
                "suspenso": "Sua conta está bloqueada. Fale com o suporte.",
                "cancelado": "Esta conta foi encerrada.",
            }.get(status_u, "Acesso não liberado.")
            return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={"detail": detalhe})
        if status_u == "pendente":
            return RedirectResponse(url="/auth/aguardando-aprovacao", status_code=302)
        msg = {
            "suspenso": "Sua+conta+esta+bloqueada.+Fale+com+o+suporte.",
            "cancelado": "Esta+conta+foi+encerrada.",
        }.get(status_u, "Acesso+nao+liberado.")
        resp = RedirectResponse(url=f"/auth/login?erro={msg}", status_code=302)
        resp.delete_cookie(key="access_token")
        return resp
