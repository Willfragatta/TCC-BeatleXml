"""Catálogo de páginas do painel e regras de acesso por papel."""
from typing import FrozenSet, Iterable, List, Optional, Set, Tuple

from app.models.database import Usuario, UsuarioEmpresa

PAPEIS_ESCRITA = {"empresa_master", "operador"}

PAGINAS_CATALOGO: List[Tuple[str, str]] = [
    ("/dashboard", "Dashboard"),
    ("/notas", "NFe recebidas"),
    ("/nfe-emitidas", "NFe emitidas"),
    ("/nfce-emitidas", "NFCe emitidas"),
    ("/financeiro", "Financeiro"),
    ("/empresas", "Empresas"),
    ("/usuarios", "Usuários"),
    ("/contador", "Contador"),
    ("/ferramentas", "Ferramentas"),
    ("/config", "Configurações"),
]

PAGINAS_OPCOES = [p for p, _ in PAGINAS_CATALOGO]
PAGINAS_TODAS: FrozenSet[str] = frozenset(PAGINAS_OPCOES)

PAGINAS_PADRAO = {
    "empresa_master": set(PAGINAS_OPCOES),
    "operador": {
        "/dashboard", "/notas", "/nfe-emitidas", "/nfce-emitidas",
        "/financeiro", "/contador", "/ferramentas", "/config",
    },
    "leitura": {
        "/dashboard", "/notas", "/nfe-emitidas", "/nfce-emitidas",
    },
}

# Escritório: documentos, empresas vinculadas e painel do contador.
PAGINAS_CONTADOR = {
    "/dashboard",
    "/notas",
    "/nfe-emitidas",
    "/nfce-emitidas",
    "/empresas",
    "/contador",
    "/config",
}

# Prefixo da URL -> página do catálogo.
API_PARA_PAGINA = (
    ("/api/dashboard", "/dashboard"),
    ("/api/notas", "/notas"),
    ("/api/etiquetas", "/notas"),
    ("/api/exportacao", "/notas"),
    ("/api/financeiro", "/financeiro"),
    ("/api/empresas", "/empresas"),
    ("/api/usuarios", "/usuarios"),
    ("/config/", "/config"),
    ("/config", "/config"),
    ("/sync", "/notas"),
    ("/nota/", "/notas"),
    ("/download/", "/notas"),
    ("/importar-manual", "/notas"),
)

# Rotas autenticadas que não passam pelo filtro de páginas do cliente.
PREFIXOS_SEM_PAGINA = (
    "/api/uploader",
    "/api/contador",
    "/api/admin",
    "/api/suporte",
    "/suporte",
    "/auth/",
)


def usuario_eh_staff(usuario: Optional[Usuario]) -> bool:
    if usuario is None:
        return False
    if getattr(usuario, "is_admin", False):
        return True
    return getattr(usuario, "role", None) in ("suporte", "admin")


def _normalizar_lista(paginas: Optional[Iterable]) -> Optional[Set[str]]:
    if paginas is None:
        return None
    validas = {str(p) for p in paginas if str(p) in PAGINAS_TODAS}
    return validas or None


def paginas_do_papel(papel: str) -> Set[str]:
    return set(PAGINAS_PADRAO.get(papel) or PAGINAS_PADRAO["operador"])


def paginas_efetivas(
    vinculo: Optional[UsuarioEmpresa],
    papel: str,
) -> Set[str]:
    """Lista efetiva: customizada no vínculo ou padrão do papel."""
    if vinculo is not None:
        custom = _normalizar_lista(vinculo.paginas_permitidas)
        if custom is not None:
            return custom
    return paginas_do_papel(papel)


def pagina_do_path(path: str) -> Optional[str]:
    """Melhor match de página HTML para um path."""
    melhor = None
    melhor_len = -1
    for pagina in PAGINAS_OPCOES:
        if path == pagina or path.startswith(pagina + "/") or path.startswith(pagina + "?"):
            if len(pagina) > melhor_len:
                melhor = pagina
                melhor_len = len(pagina)
    return melhor


def pagina_exigida_api(path: str) -> Optional[str]:
    """Página do catálogo exigida para um path de API/ação."""
    for prefixo, pagina in API_PARA_PAGINA:
        if path == prefixo or path.startswith(prefixo):
            return pagina
    return pagina_do_path(path)


def path_exige_pagina(path: str) -> bool:
    if any(path == p or path.startswith(p) for p in PREFIXOS_SEM_PAGINA):
        return False
    if path.startswith("/static"):
        return False
    return True


def acesso_pagina_ok(paginas: Optional[Set[str]], path: str) -> bool:
    """paginas is None = acesso total (staff)."""
    if paginas is None:
        return True
    if not path_exige_pagina(path):
        return True
    exigida = pagina_exigida_api(path)
    if exigida is None:
        html = pagina_do_path(path)
        if html is None:
            return True
        return html in paginas
    return exigida in paginas


def primeira_pagina(paginas: Optional[Set[str]]) -> str:
    if not paginas:
        return "/dashboard"
    for p, _ in PAGINAS_CATALOGO:
        if p in paginas:
            return p
    return "/dashboard"
