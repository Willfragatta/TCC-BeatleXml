from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.routers import documentos, empresas, health

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)

app = FastAPI(
    title=settings.app_name,
    description="Plataforma de centralização de documentos fiscais e bancários",
    version="0.1.0",
)

app.include_router(health.router)
app.include_router(empresas.router, prefix="/api/empresas", tags=["empresas"])
app.include_router(documentos.router, prefix="/api/documentos", tags=["documentos"])


@app.get("/", response_class=HTMLResponse)
async def pagina_inicial(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"titulo": settings.app_name},
    )
