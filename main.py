import os
import re
import logging
from fastapi import FastAPI, Request, Depends, BackgroundTasks, HTTPException
from fastapi.responses import Response, RedirectResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy.orm import Session
from app.models.database import SessionLocal, NotaFiscal, Empresa
from app.routers import auth
from app.core.middleware import AuthMiddleware, CanonicalHostMiddleware, SecurityHeadersMiddleware
from app.core.dependencies import get_usuario_atual, get_empresa_ativa, get_db
from app.core.settings import (
    is_production,
    contato_email,
    site_url,
    empresa_razao,
    empresa_cnpj,
    empresa_cidade,
)
from app.core.templates import templates
import worker
import uvicorn
from fastapi.staticfiles import StaticFiles
from app.services.parser import XMLParser, decodificar_xml_bytes
from app.services.parcelas import gravar_parcelas
from datetime import datetime, timedelta
from typing import Any, Optional

_hide_docs = is_production()
app = FastAPI(
    title="Beatle_XML",
    version="1.0.0",
    docs_url=None if _hide_docs else "/docs",
    redoc_url=None if _hide_docs else "/redoc",
    openapi_url=None if _hide_docs else "/openapi.json",
)
app.mount("/static", StaticFiles(directory="static"), name="static")

# CORS: origins explícitas (CORS_ORIGINS=http://localhost:8000,https://beatlexml.com.br)
_cors_raw = (os.getenv("CORS_ORIGINS") or "http://localhost:8000,http://127.0.0.1:8000").strip()
_cors_origins = [o.strip() for o in _cors_raw.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
)

# Headers de segurança (após CORS, antes do Auth)
app.add_middleware(SecurityHeadersMiddleware)
# Middleware de autenticação
app.add_middleware(AuthMiddleware)
# Por último na pilha = executa primeiro: apex → www
app.add_middleware(CanonicalHostMiddleware)

# Incluir routers
app.include_router(auth.router, prefix="/auth", tags=["Autenticação"])
from app.routers import etiquetas, notas as notas_router, dashboard, empresas, usuarios, exportacao, admin, config, suporte, financeiro, uploader, publico, contador_agente
app.include_router(etiquetas.router, prefix="/api/etiquetas", tags=["Etiquetas"])
app.include_router(notas_router.router, prefix="/api/notas", tags=["Notas"])
app.include_router(financeiro.router, prefix="/api/financeiro", tags=["Financeiro"])
app.include_router(dashboard.router, prefix="/api/dashboard", tags=["Dashboard"])
app.include_router(empresas.router, prefix="/api/empresas", tags=["Empresas"])
app.include_router(usuarios.router, prefix="/api/usuarios", tags=["Usuários"])
app.include_router(exportacao.router, prefix="/api/exportacao", tags=["Exportação"])
app.include_router(admin.router, prefix="/api/admin", tags=["Admin"])
app.include_router(config.router, prefix="/config", tags=["Configurações"])
app.include_router(suporte.router, tags=["Suporte"])
app.include_router(uploader.router, prefix="/api/uploader", tags=["Uploader"])
app.include_router(contador_agente.router, prefix="/api/contador", tags=["Contador"])
app.include_router(publico.router, tags=["Público"])

logger = logging.getLogger("beatle_xml")
_is_production = is_production() or os.getenv("DEBUG", "").lower() in ("0", "false", "no")


def _quer_html(request: Request) -> bool:
    """True quando a resposta deve ser pagina HTML (nao JSON de API)."""
    path = request.url.path or ""
    if path.startswith("/api/"):
        return False
    accept = (request.headers.get("accept") or "").lower()
    if "application/json" in accept and "text/html" not in accept:
        return False
    return True


def _detail_texto(detail: Any) -> str:
    if detail is None:
        return "Nao foi possivel concluir esta solicitacao."
    if isinstance(detail, str):
        return detail
    if isinstance(detail, dict):
        return str(detail.get("mensagem") or detail.get("detail") or detail)
    if isinstance(detail, list) and detail:
        primeiro = detail[0]
        if isinstance(primeiro, dict):
            return str(primeiro.get("msg") or primeiro)
        return str(primeiro)
    return str(detail)


@app.exception_handler(StarletteHTTPException)
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """HTML amigavel no painel; JSON nas APIs. Evita JSON cru no browser.
    Cobre FastAPI HTTPException e Starlette (404 de rota inexistente)."""
    detalhe = _detail_texto(getattr(exc, "detail", None))
    path = request.url.path or ""

    # Onboarding: sem empresa -> cadastrar loja (nao mostrar pagina de erro)
    if (
        _quer_html(request)
        and "Nenhuma empresa cadastrada" in detalhe
        and not path.startswith("/api/")
    ):
        return RedirectResponse(url="/empresas", status_code=302)

    if not _quer_html(request):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    codigo = exc.status_code
    if codigo == 404:
        return templates.TemplateResponse(
            request,
            "erros/404.html",
            {
                "codigo": 404,
                "titulo": "Pagina nao encontrada",
                "mensagem": detalhe if detalhe and "Nenhuma empresa" not in detalhe
                else "O endereco que voce tentou abrir nao existe ou foi movido.",
                "icone": "fa-compass",
                "cta_href": "/empresas" if "empresa" in detalhe.lower() else "/dashboard",
                "cta_label": "Cadastrar empresa" if "empresa" in detalhe.lower() else "Ir ao painel",
            },
            status_code=404,
        )

    titulos = {
        401: "Sessao necessaria",
        403: "Acesso nao permitido",
        402: "Pagamento pendente",
        400: "Solicitacao invalida",
        422: "Dados invalidos",
        500: "Erro interno",
    }
    icones = {
        401: "fa-lock",
        403: "fa-ban",
        402: "fa-credit-card",
        400: "fa-circle-exclamation",
        500: "fa-triangle-exclamation",
    }
    return templates.TemplateResponse(
        request,
        "erros/generico.html",
        {
            "codigo": codigo,
            "titulo": titulos.get(codigo, "Algo deu errado"),
            "mensagem": detalhe,
            "icone": icones.get(codigo, "fa-exclamation-triangle"),
            "badge_bg": "bg-rose-50" if codigo >= 500 else "bg-amber-50",
            "badge_fg": "text-rose-600" if codigo >= 500 else "text-amber-600",
            "cta_href": "/auth/login" if codigo in (401, 403) else "/dashboard",
            "cta_label": "Ir para o login" if codigo in (401, 403) else "Voltar ao painel",
        },
        status_code=codigo,
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Em producao retorna mensagem generica; detalhes vao so para o log."""
    logger.exception("Erro nao tratado: %s", exc)
    if not _is_production:
        raise exc
    if _quer_html(request):
        return templates.TemplateResponse(
            request,
            "erros/generico.html",
            {
                "codigo": 500,
                "titulo": "Erro interno",
                "mensagem": "Erro interno. Tente novamente mais tarde.",
                "icone": "fa-triangle-exclamation",
                "badge_bg": "bg-rose-50",
                "badge_fg": "text-rose-600",
                "cta_href": "/dashboard",
                "cta_label": "Voltar ao painel",
            },
            status_code=500,
        )
    return JSONResponse(
        status_code=500,
        content={"detail": "Erro interno. Tente novamente mais tarde."},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Em producao evita expor detalhes de validacao em respostas."""
    if _quer_html(request) and _is_production:
        return templates.TemplateResponse(
            request,
            "erros/generico.html",
            {
                "codigo": 422,
                "titulo": "Dados invalidos",
                "mensagem": "Verifique os campos e tente novamente.",
                "icone": "fa-circle-exclamation",
                "cta_href": "/dashboard",
            },
            status_code=422,
        )
    if _is_production:
        return JSONResponse(
            status_code=422,
            content={"detail": "Dados inválidos. Verifique os campos."},
        )
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.get("/")
async def root(request: Request):
    """Landing pública; redireciona se autenticado e ativo."""
    token = request.cookies.get("access_token")
    if token:
        from app.core.auth import verificar_token
        usuario_id = verificar_token(token)
        if usuario_id:
            db = SessionLocal()
            try:
                from app.models.database import Usuario
                usuario = db.query(Usuario).filter_by(id=usuario_id).first()
                if usuario and (getattr(usuario, "status", "ativo") or "ativo") == "ativo":
                    if getattr(usuario, "role", None) == "suporte" or getattr(usuario, "is_admin", False):
                        return RedirectResponse(url="/suporte", status_code=302)
                    empresa = db.query(Empresa).filter_by(usuario_id=usuario_id).first()
                    if empresa:
                        return RedirectResponse(url="/dashboard", status_code=302)
                    return RedirectResponse(url="/empresas", status_code=302)
                if usuario and getattr(usuario, "status", None) == "pendente":
                    return RedirectResponse(url="/auth/aguardando-aprovacao", status_code=302)
            except Exception as e:
                logger.warning("Falha ao redirecionar usuário autenticado na home: %s", e)
            finally:
                db.close()

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "contato_email": contato_email(),
            "ano": datetime.now().year,
            "meta_description": (
                "XML e DANFE para a loja e para o escritório contábil. "
                "O BeatleXML organiza as notas da Receita e os cupons enviados pelo caixa."
            ),
            "og_title": "BeatleXML — notas fiscais para empresas e contadores",
            "empresa_razao": empresa_razao(),
            "empresa_cnpj": empresa_cnpj(),
            "empresa_cidade": empresa_cidade(),
            "canonical": site_url() + "/",
        },
    )

@app.get("/dashboard")
async def dashboard_page(request: Request, usuario = Depends(get_usuario_atual), empresa = Depends(get_empresa_ativa)):
    """Dashboard principal (protegido). Contador abre o buscador de empresas."""
    template = "dashboard/contador.html" if getattr(usuario, "role", None) == "contador" else "dashboard/index.html"
    return templates.TemplateResponse(
        request,
        template,
        {"usuario": usuario, "empresa": empresa},
    )

@app.get("/empresas")
async def empresas_page(request: Request, usuario = Depends(get_usuario_atual)):
    """Página de gestão de empresas (protegido)."""
    return templates.TemplateResponse(
        request,
        "empresas/listar.html",
        {"usuario": usuario},
    )

@app.get("/usuarios")
async def usuarios_page(request: Request, usuario = Depends(get_usuario_atual)):
    """Página de gestão de usuários (protegido)."""
    return templates.TemplateResponse(
        request,
        "usuarios/listar.html",
        {"usuario": usuario},
    )

@app.get("/notas")
async def listar_notas(
    request: Request, 
    usuario = Depends(get_usuario_atual),
    empresa = Depends(get_empresa_ativa),
    db: Session = Depends(get_db)
):
    """Lista de notas (protegido) - Template inicial, dados carregados via API."""
    # Template inicial - dados serão carregados via API JavaScript
    return templates.TemplateResponse(
        request,
        "notas/listar.html",
        {
            "notas": [],
            "usuario": usuario,
            "empresa": empresa,
        },
    )


@app.get("/ferramentas")
async def ferramentas_page(
    request: Request,
    usuario=Depends(get_usuario_atual),
):
    """Página de download do Beatle Uploader."""
    return templates.TemplateResponse(
        request,
        "ferramentas/index.html",
        {"usuario": usuario},
    )


@app.get("/nfe-emitidas")
async def nfe_emitidas_page(
    request: Request,
    usuario=Depends(get_usuario_atual),
    empresa=Depends(get_empresa_ativa),
):
    """Listagem de NFe emitidas pela empresa (uploader)."""
    return templates.TemplateResponse(
        request,
        "notas_emitidas/listar.html",
        {
            "usuario": usuario,
            "empresa": empresa,
            "titulo": "NFe emitidas",
            "modelo": "55",
        },
    )


@app.get("/nfce-emitidas")
async def nfce_emitidas_page(
    request: Request,
    usuario=Depends(get_usuario_atual),
    empresa=Depends(get_empresa_ativa),
):
    """Listagem de NFCe emitidas pela empresa (uploader)."""
    return templates.TemplateResponse(
        request,
        "notas_emitidas/listar.html",
        {
            "usuario": usuario,
            "empresa": empresa,
            "titulo": "NFCe emitidas",
            "modelo": "65",
        },
    )


@app.get("/contador")
async def contador_page(
    request: Request,
    usuario=Depends(get_usuario_atual),
    empresa=Depends(get_empresa_ativa),
):
    """Página do contador: download de notas por mês/ano (emitidas)."""
    return templates.TemplateResponse(
        request,
        "contador/index.html",
        {"usuario": usuario, "empresa": empresa},
    )


@app.get("/financeiro")
async def financeiro_page(
    request: Request,
    usuario = Depends(get_usuario_atual),
    empresa = Depends(get_empresa_ativa),
):
    """Página Financeiro (A Pagar) - listagem de parcelas e totais."""
    return templates.TemplateResponse(
        request,
        "financeiro/listar.html",
        {"usuario": usuario, "empresa": empresa},
    )

@app.post("/importar-manual")
def importar_arquivos_locais(
    usuario=Depends(get_usuario_atual),
    empresa=Depends(get_empresa_ativa),
    db: Session = Depends(get_db),
):
    caminho_import = "data/import_arquivei"
    
    if not os.path.exists(caminho_import):
        os.makedirs(caminho_import)
        return {"aviso": "Pasta criada. Cole os XMLs em 'data/import_arquivei' e tente novamente."}

    arquivos = [f for f in os.listdir(caminho_import) if f.endswith(".xml")]
    sucesso = 0
    erros = 0

    if empresa is None:
        raise HTTPException(status_code=400, detail="Nenhuma empresa ativa.")

    for arquivo in arquivos:
        try:
            caminho_completo = os.path.join(caminho_import, arquivo)
            with open(caminho_completo, "rb") as f:
                conteudo_xml = decodificar_xml_bytes(f.read())

            # Usa o seu parser já existente
            parser = XMLParser(conteudo_xml)
            info = parser.extrair_todos_os_dados()
            
            # Verifica se a nota já existe pela chave
            chave = re.search(r'chNFe="(\d+)"|chNFe>(\d+)<', conteudo_xml)
            chave_limpa = chave.group(1) or chave.group(2) if chave else arquivo.replace(".xml", "")

            nota_existente = db.query(NotaFiscal).filter_by(chave_acesso=chave_limpa).first()

            if not nota_existente:
                nova_nota = NotaFiscal(
                    empresa_id=empresa.id,
                    chave_acesso=chave_limpa,
                    numero_nfe=info["numero"],
                    serie=info.get("serie"),
                    emitente_nome=info["emitente"],
                    emitente_cnpj=info.get("emitente_cnpj"),
                    natureza_operacao=info.get("natureza_operacao"),
                    valor_total=info["valor"],
                    data_emissao=info.get("data") or datetime.now(),
                    xml_conteudo=conteudo_xml,
                    status_manifesto=info["tipo_operacao"]
                )
                db.add(nova_nota)
                db.flush()
                gravar_parcelas(db, nova_nota)
                sucesso += 1
            else:
                # Se a nota já existia mas era apenas resumo, atualiza para completa
                if "RESUMO" in parser.identificar_tipo() or not nota_existente.numero_nfe:
                    nota_existente.xml_conteudo = conteudo_xml
                    nota_existente.numero_nfe = info["numero"]
                    nota_existente.serie = info.get("serie")
                    if info.get("emitente_cnpj"):
                        nota_existente.emitente_cnpj = info.get("emitente_cnpj")
                    if info.get("natureza_operacao"):
                        nota_existente.natureza_operacao = info.get("natureza_operacao")
                    nota_existente.status_manifesto = "COMPLETA"
                    db.flush()
                    gravar_parcelas(db, nota_existente)
                    sucesso += 1

            db.commit()
        except Exception as e:
            print(f"Erro ao importar {arquivo}: {e}")
            db.rollback()
            erros += 1

    return {"status": "Processamento concluído", "sucesso": sucesso, "erros": erros}

@app.get("/sync/status")
async def status_sincronizacao(
    usuario = Depends(get_usuario_atual),
    empresa = Depends(get_empresa_ativa),
    db: Session = Depends(get_db)
):
    """Retorna o status atual da sincronização para o frontend."""
    agora = datetime.now()
    
    # Verifica bloqueio por 656
    bloqueado_656 = False
    tempo_restante_656 = 0
    if empresa.bloqueio_656_ate and agora < empresa.bloqueio_656_ate:
        bloqueado_656 = True
        tempo_restante_656 = int((empresa.bloqueio_656_ate - agora).total_seconds())

    # Verifica bloqueio manual (suporte)
    bloqueado_manual = False
    tempo_restante_manual = 0
    if empresa.bloqueio_manual_ate and agora < empresa.bloqueio_manual_ate:
        bloqueado_manual = True
        tempo_restante_manual = int((empresa.bloqueio_manual_ate - agora).total_seconds())
    
    # Verifica se está no fim da fila
    fim_da_fila = False
    if empresa.ultimo_nsu and empresa.max_nsu_conhecido:
        try:
            ult_nsu_int = int(empresa.ultimo_nsu)
            max_nsu_int = int(empresa.max_nsu_conhecido)
            fim_da_fila = ult_nsu_int >= max_nsu_int
        except:
            pass
    
    # Verifica próxima consulta periódica (quando está no fim da fila)
    bloqueado_consulta_periodica = False
    tempo_restante_periodica = 0
    if fim_da_fila and empresa.proxima_consulta_periodica and agora < empresa.proxima_consulta_periodica:
        bloqueado_consulta_periodica = True
        tempo_restante_periodica = int((empresa.proxima_consulta_periodica - agora).total_seconds())
    
    # Verifica intervalo mínimo (1 hora entre sincronizações)
    pode_sincronizar = True
    tempo_restante_intervalo = 0
    motivo_bloqueio = None
    mensagem_bloqueio = None
    
    if bloqueado_manual:
        pode_sincronizar = False
        motivo_bloqueio = "bloqueio_manual"
        tempo_restante_intervalo = tempo_restante_manual
        horas = int(tempo_restante_manual / 3600)
        minutos = int((tempo_restante_manual % 3600) / 60)
        if horas > 0:
            mensagem_bloqueio = f"Sincronização pausada pelo suporte. Aguarde {horas} hora(s) e {minutos} minuto(s)."
        else:
            mensagem_bloqueio = f"Sincronização pausada pelo suporte. Aguarde {minutos} minuto(s)."
    elif bloqueado_656:
        pode_sincronizar = False
        motivo_bloqueio = "bloqueio_656"
        tempo_restante_intervalo = tempo_restante_656
        horas = int(tempo_restante_656 / 3600)
        minutos = int((tempo_restante_656 % 3600) / 60)
        if horas > 0:
            mensagem_bloqueio = f"Sistema aguardando liberação da Receita Federal. Isso é normal e acontece automaticamente. Aguarde aproximadamente {horas} hora(s) e {minutos} minuto(s). Você não precisa fazer nada - o sistema continuará automaticamente."
        else:
            mensagem_bloqueio = f"Sistema aguardando liberação da Receita Federal. Isso é normal e acontece automaticamente. Aguarde aproximadamente {minutos} minuto(s). Você não precisa fazer nada - o sistema continuará automaticamente."
    elif bloqueado_consulta_periodica:
        pode_sincronizar = False
        motivo_bloqueio = "consulta_periodica"
        tempo_restante_intervalo = tempo_restante_periodica
        horas = int(tempo_restante_periodica / 3600)
        minutos = int((tempo_restante_periodica % 3600) / 60)
        if horas > 0:
            mensagem_bloqueio = f"Todos os documentos disponíveis foram baixados. O sistema verificará automaticamente se há novos documentos em aproximadamente {horas} hora(s) e {minutos} minuto(s). Isso é normal - você não precisa fazer nada."
        else:
            mensagem_bloqueio = f"Todos os documentos disponíveis foram baixados. O sistema verificará automaticamente se há novos documentos em aproximadamente {minutos} minuto(s). Isso é normal - você não precisa fazer nada."
    elif empresa.ultima_consulta:
        tempo_desde_ultima = agora - empresa.ultima_consulta
        # Intervalo mínimo: 15 min quando fila grande, 1h quando fim da fila
        intervalo_minimo = timedelta(hours=1) if fim_da_fila else timedelta(minutes=5)
        
        if tempo_desde_ultima < intervalo_minimo:
            pode_sincronizar = False
            motivo_bloqueio = "intervalo_minimo"
            tempo_restante_intervalo = int((intervalo_minimo - tempo_desde_ultima).total_seconds())
            horas = int(tempo_restante_intervalo / 3600)
            minutos = int((tempo_restante_intervalo % 3600) / 60)
            if horas > 0:
                mensagem_bloqueio = f"Respeitando limites da Receita Federal. Aguarde {horas} hora(s) e {minutos} minuto(s) antes de sincronizar novamente. Isso evita bloqueios e garante funcionamento contínuo."
            else:
                mensagem_bloqueio = f"Respeitando limites da Receita Federal. Aguarde {minutos} minuto(s) antes de sincronizar novamente. Isso evita bloqueios e garante funcionamento contínuo."
    
    return {
        "pode_sincronizar": pode_sincronizar,
        "bloqueado_656": bloqueado_656,
        "bloqueado_manual": bloqueado_manual,
        "fim_da_fila": fim_da_fila,
        "bloqueado_consulta_periodica": bloqueado_consulta_periodica,
        "tempo_restante_segundos": tempo_restante_intervalo,
        "motivo_bloqueio": motivo_bloqueio,
        "mensagem_bloqueio": mensagem_bloqueio,
        "ultima_consulta": empresa.ultima_consulta.isoformat() if empresa.ultima_consulta else None,
        "ultima_sync_mensagem": empresa.ultima_sync_mensagem,
        "proxima_consulta_periodica": empresa.proxima_consulta_periodica.isoformat() if empresa.proxima_consulta_periodica else None,
        "ultimo_nsu": empresa.ultimo_nsu,
        "max_nsu_conhecido": empresa.max_nsu_conhecido
    }

@app.post("/sync")
async def sincronizar(
    background_tasks: BackgroundTasks, 
    usuario = Depends(get_usuario_atual),
    empresa = Depends(get_empresa_ativa),
    db: Session = Depends(get_db)
):
    agora = datetime.now()
    
    # Verifica se empresa está aprovada
    if empresa.status != 'aprovado':
        raise HTTPException(
            status_code=403,
            detail=f"Empresa não aprovada. Status atual: {empresa.status}. Aguarde aprovação do administrador."
        )
    
    # Verifica bloqueio por 656 (2 horas)
    if empresa.bloqueio_656_ate and agora < empresa.bloqueio_656_ate:
        tempo_restante = empresa.bloqueio_656_ate - agora
        minutos = int(tempo_restante.total_seconds() // 60)
        segundos = int(tempo_restante.total_seconds() % 60)
        horas = int(tempo_restante.total_seconds() / 3600)
        minutos_restantes = int((tempo_restante.total_seconds() % 3600) / 60)
        if horas > 0:
            mensagem = f"Sistema aguardando liberação da Receita Federal. Isso é normal e acontece automaticamente. Aguarde aproximadamente {horas} hora(s) e {minutos_restantes} minuto(s). Você não precisa fazer nada - o sistema continuará automaticamente quando liberado."
        else:
            mensagem = f"Sistema aguardando liberação da Receita Federal. Isso é normal e acontece automaticamente. Aguarde aproximadamente {minutos_restantes} minuto(s). Você não precisa fazer nada - o sistema continuará automaticamente quando liberado."
        raise HTTPException(
            status_code=403,
            detail=mensagem,
            bloqueado=True,
            tempo_restante_segundos=int(tempo_restante.total_seconds())
        )

    # Verifica bloqueio manual (suporte)
    if empresa.bloqueio_manual_ate and agora < empresa.bloqueio_manual_ate:
        tempo_restante = empresa.bloqueio_manual_ate - agora
        horas = int(tempo_restante.total_seconds() / 3600)
        minutos_restantes = int((tempo_restante.total_seconds() % 3600) / 60)
        if horas > 0:
            mensagem = f"Sincronização pausada pelo suporte. Aguarde {horas} hora(s) e {minutos_restantes} minuto(s)."
        else:
            mensagem = f"Sincronização pausada pelo suporte. Aguarde {minutos_restantes} minuto(s)."
        raise HTTPException(
            status_code=403,
            detail=mensagem,
            bloqueado=True,
            tempo_restante_segundos=int(tempo_restante.total_seconds())
        )
    
    # Verifica se está no fim da fila e próxima consulta periódica
    fim_da_fila = False
    if empresa.ultimo_nsu and empresa.max_nsu_conhecido:
        try:
            ult_nsu_int = int(empresa.ultimo_nsu)
            max_nsu_int = int(empresa.max_nsu_conhecido)
            fim_da_fila = ult_nsu_int >= max_nsu_int
        except:
            pass
    
    if fim_da_fila and empresa.proxima_consulta_periodica and agora < empresa.proxima_consulta_periodica:
        tempo_restante = empresa.proxima_consulta_periodica - agora
        minutos = int(tempo_restante.total_seconds() // 60)
        segundos = int(tempo_restante.total_seconds() % 60)
        horas = int(tempo_restante.total_seconds() / 3600)
        minutos_restantes = int((tempo_restante.total_seconds() % 3600) / 60)
        if horas > 0:
            mensagem = f"Todos os documentos disponíveis foram baixados. O sistema verificará automaticamente se há novos documentos em aproximadamente {horas} hora(s) e {minutos_restantes} minuto(s). Isso é normal - você não precisa fazer nada."
        else:
            mensagem = f"Todos os documentos disponíveis foram baixados. O sistema verificará automaticamente se há novos documentos em aproximadamente {minutos_restantes} minuto(s). Isso é normal - você não precisa fazer nada."
        raise HTTPException(
            status_code=429,
            detail=mensagem,
            bloqueado=False,
            tempo_restante_segundos=int(tempo_restante.total_seconds())
        )
    
    # Verifica intervalo mínimo (15 min fila grande, 1h fim da fila)
    fim_da_fila_post = False
    if empresa.ultimo_nsu and empresa.max_nsu_conhecido:
        try:
            fim_da_fila_post = int(empresa.ultimo_nsu) >= int(empresa.max_nsu_conhecido)
        except Exception:
            pass
    if empresa.ultima_consulta:
        tempo_desde_ultima = agora - empresa.ultima_consulta
        intervalo_minimo = timedelta(hours=1) if fim_da_fila_post else timedelta(minutes=5)
        
        if tempo_desde_ultima < intervalo_minimo:
            tempo_restante = intervalo_minimo - tempo_desde_ultima
            minutos = int(tempo_restante.total_seconds() // 60)
            segundos = int(tempo_restante.total_seconds() % 60)
            horas = int(tempo_restante.total_seconds() / 3600)
            minutos_restantes = int((tempo_restante.total_seconds() % 3600) / 60)
            if horas > 0:
                mensagem = f"Respeitando limites da Receita Federal. Aguarde {horas} hora(s) e {minutos_restantes} minuto(s) antes de sincronizar novamente. Isso evita bloqueios e garante funcionamento contínuo."
            else:
                mensagem = f"Respeitando limites da Receita Federal. Aguarde {minutos_restantes} minuto(s) antes de sincronizar novamente. Isso evita bloqueios e garante funcionamento contínuo."
            raise HTTPException(
                status_code=429,
                detail=mensagem,
                bloqueado=False,
                tempo_restante_segundos=int(tempo_restante.total_seconds())
            )
    
    # Inicia sincronização em background APENAS para a empresa selecionada
    background_tasks.add_task(worker.executar_consulta, empresa_id=empresa.id)
    return {"status": "Sincronização iniciada!", "empresa": empresa.razao_social, "empresa_id": empresa.id}

@app.get("/nota/{nota_id}")
async def obter_detalhes_nota(
    nota_id: int, 
    usuario = Depends(get_usuario_atual),
    empresa = Depends(get_empresa_ativa),
    db: Session = Depends(get_db)
):
    nota = db.query(NotaFiscal).filter(
        NotaFiscal.id == nota_id,
        NotaFiscal.empresa_id == empresa.id
    ).first()
    if not nota or not nota.xml_conteudo:
        raise HTTPException(status_code=404, detail="Nota não encontrada")
    
    parser = XMLParser(nota.xml_conteudo)
    dados_completos = parser.extrair_detalhes_completos()

    # Backfill natureza / CNPJ emitente
    try:
        info = parser.extrair_todos_os_dados()
        alterou = False
        if not nota.natureza_operacao and info.get("natureza_operacao"):
            nota.natureza_operacao = info.get("natureza_operacao")
            alterou = True
        if not nota.emitente_cnpj and info.get("emitente_cnpj"):
            nota.emitente_cnpj = info.get("emitente_cnpj")
            alterou = True
        if alterou:
            db.commit()
    except Exception:
        pass
    
    if not dados_completos: # Se for resumo, envia o básico
        return {
            "chave": nota.chave_acesso,
            "emitente_nome": nota.emitente_nome,
            "valor": float(nota.valor_total) if nota.valor_total else 0,
            "natureza_operacao": nota.natureza_operacao or "",
            "tipo": "RESUMO",
            "itens": []
        }

    if dados_completos.get("nfe_info") is not None and not dados_completos["nfe_info"].get("natureza_operacao"):
        dados_completos["nfe_info"]["natureza_operacao"] = nota.natureza_operacao or ""

    return {
        "chave": nota.chave_acesso,
        "dados_nfe": dados_completos["nfe_info"],
        "emitente": dados_completos["emitente"],
        "itens": dados_completos["produtos"],
        "natureza_operacao": (
            (dados_completos.get("nfe_info") or {}).get("natureza_operacao")
            or nota.natureza_operacao
            or ""
        ),
        "tipo": "COMPLETA"
    }

@app.get("/download/{nota_id}")
async def baixar_xml(
    nota_id: int,
    usuario = Depends(get_usuario_atual),
    empresa = Depends(get_empresa_ativa),
    db: Session = Depends(get_db)
):
    """Download de XML individual."""
    nota = db.query(NotaFiscal).filter(
        NotaFiscal.id == nota_id,
        NotaFiscal.empresa_id == empresa.id
    ).first()
    if not nota or not nota.xml_conteudo:
        raise HTTPException(status_code=404, detail="Nota não encontrada")
        
    return Response(
        content=nota.xml_conteudo,
        media_type="application/xml",
        headers={"Content-Disposition": f"attachment; filename={nota.chave_acesso}.xml"}
    )

if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print(f"\n{'='*60}")
    print(f"🚀 Iniciando Beatle_XML na porta {port}")
    print(f"{'='*60}\n")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")