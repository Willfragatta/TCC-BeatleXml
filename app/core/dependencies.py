from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer
from sqlalchemy import text
from sqlalchemy.orm import Session
from typing import List, Optional, Set
from app.models.database import SessionLocal, Usuario, Empresa, UsuarioEmpresa
from app.core.paginas import (
    PAPEIS_ESCRITA,
    paginas_efetivas,
    usuario_eh_staff,
)

security = HTTPBearer()


def aplicar_contexto_rls(db: Session, request: Optional[Request] = None) -> None:
    """SET LOCAL de empresa/usuario para preparar RLS (Fase B). Custo desprezível."""
    uid = ""
    eid = ""
    role = "anon"
    if request is not None:
        usuario = getattr(request.state, "usuario", None)
        if usuario is not None:
            uid = str(getattr(usuario, "id", "") or "")
            role = "staff" if usuario_eh_staff(usuario) else "cliente"
        cookie_emp = request.cookies.get("empresa_ativa") if request.cookies else None
        if cookie_emp:
            eid = str(cookie_emp)
    try:
        db.execute(text("SELECT set_config('app.usuario_id', :v, true)"), {"v": uid})
        db.execute(text("SELECT set_config('app.empresa_id', :v, true)"), {"v": eid})
        db.execute(text("SELECT set_config('app.role', :v, true)"), {"v": role})
    except Exception:
        pass


def get_db(request: Request):
    """Dependency para obter sessão do banco."""
    db = SessionLocal()
    try:
        aplicar_contexto_rls(db, request)
        yield db
    finally:
        db.close()


def get_usuario_atual(request: Request, db: Session = Depends(get_db)) -> Usuario:
    """Usuário autenticado, ligado à sessão desta requisição."""
    if not hasattr(request.state, "usuario"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuário não autenticado"
        )
    usuario_id = getattr(request.state, "usuario_id", None) or request.state.usuario.id
    atual = db.get(Usuario, usuario_id)
    if atual is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuário não autenticado"
        )
    return atual


def ids_empresas_acessiveis(db: Session, usuario: Usuario) -> List[int]:
    """Empresas do usuário: dono (usuario_id) + vínculos UsuarioEmpresa."""
    ids: Set[int] = set()
    for (eid,) in db.query(Empresa.id).filter_by(usuario_id=usuario.id).all():
        ids.add(eid)
    for (eid,) in db.query(UsuarioEmpresa.empresa_id).filter_by(usuario_id=usuario.id).all():
        ids.add(eid)
    return sorted(ids)


def vinculo_empresa(
    db: Session, usuario: Usuario, empresa_id: int
) -> Optional[UsuarioEmpresa]:
    return (
        db.query(UsuarioEmpresa)
        .filter_by(usuario_id=usuario.id, empresa_id=empresa_id)
        .first()
    )


def papel_na_empresa(db: Session, usuario: Usuario, empresa: Empresa) -> str:
    """Papel efetivo na empresa. Dono sem vínculo = empresa_master."""
    if getattr(usuario, "is_admin", False):
        return "empresa_master"
    vinculo = vinculo_empresa(db, usuario, empresa.id)
    if vinculo and vinculo.papel:
        return str(vinculo.papel)
    if empresa.usuario_id == usuario.id:
        return "empresa_master"
    return "operador"


def usuario_acessa_empresa(db: Session, usuario: Usuario, empresa_id: int) -> bool:
    if getattr(usuario, "is_admin", False):
        return True
    if getattr(usuario, "role", None) == "suporte":
        return True
    return empresa_id in ids_empresas_acessiveis(db, usuario)


def get_empresa_ativa(
    request: Request,
    usuario: Usuario = Depends(get_usuario_atual),
    db: Session = Depends(get_db)
) -> Empresa:
    """Empresa ativa: cookie, dono ou vínculo UsuarioEmpresa."""
    if getattr(usuario, "role", None) == "suporte" and not getattr(usuario, "is_admin", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Usuário de suporte não possui empresa ativa"
        )

    acessiveis = ids_empresas_acessiveis(db, usuario)
    if getattr(usuario, "is_admin", False) and not acessiveis:
        # Admin pode operar qualquer empresa via cookie
        empresa_id = request.cookies.get("empresa_ativa")
        if empresa_id:
            try:
                empresa = db.query(Empresa).filter_by(id=int(empresa_id)).first()
                if empresa:
                    return empresa
            except Exception:
                pass

    empresa_id = request.cookies.get("empresa_ativa")
    if empresa_id:
        try:
            eid = int(empresa_id)
            if eid in acessiveis or getattr(usuario, "is_admin", False):
                empresa = db.query(Empresa).filter_by(id=eid).first()
                if empresa:
                    return empresa
        except Exception:
            pass

    if not acessiveis:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Nenhuma empresa cadastrada"
        )

    empresa = db.query(Empresa).filter(Empresa.id.in_(acessiveis)).first()
    if not empresa:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Nenhuma empresa cadastrada"
        )
    return empresa


def paginas_do_usuario(db: Session, usuario: Usuario, empresa: Optional[Empresa] = None) -> Optional[Set[str]]:
    """None = acesso total (staff). Caso contrário, set de paths liberados."""
    if usuario_eh_staff(usuario):
        return None

    # Sem nenhuma empresa: so onboarding (cadastrar loja + contato).
    # Evita loop login -> /empresas bloqueado -> /dashboard -> JSON "Nenhuma empresa".
    acessiveis = ids_empresas_acessiveis(db, usuario)
    if not acessiveis:
        return {"/empresas"}

    papel = "operador"
    vinculo = None
    if empresa is not None:
        papel = papel_na_empresa(db, usuario, empresa)
        vinculo = vinculo_empresa(db, usuario, empresa.id)
    if getattr(usuario, "role", None) == "contador":
        from app.core.paginas import PAGINAS_CONTADOR
        # O papel na empresa não tira do contador as páginas do escritório.
        # Uma lista gravada no vínculo ainda pode reduzir esse conjunto.
        if vinculo is not None and vinculo.paginas_permitidas:
            return paginas_efetivas(vinculo, papel) & PAGINAS_CONTADOR
        return set(PAGINAS_CONTADOR)
    return paginas_efetivas(vinculo, papel)


def get_suporte_atual(
    usuario: Usuario = Depends(get_usuario_atual)
) -> Usuario:
    """Dependency para garantir acesso de suporte."""
    if usuario_eh_staff(usuario):
        return usuario
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Acesso restrito ao suporte"
    )


def exige_papel_master(
    request: Request,
    usuario: Usuario = Depends(get_usuario_atual),
    db: Session = Depends(get_db),
) -> Usuario:
    if getattr(usuario, "is_admin", False) or getattr(usuario, "role", None) == "suporte":
        return usuario
    empresa = get_empresa_ativa(request, usuario, db)
    papel = papel_na_empresa(db, usuario, empresa)
    if papel != "empresa_master":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Somente o master da empresa pode gerenciar usuários"
        )
    return usuario


def exige_papel_escrita(
    request: Request,
    usuario: Usuario = Depends(get_usuario_atual),
    db: Session = Depends(get_db),
) -> Usuario:
    """Bloqueia mutações para perfil somente leitura."""
    if getattr(usuario, "is_admin", False) or getattr(usuario, "role", None) == "suporte":
        return usuario
    empresa = get_empresa_ativa(request, usuario, db)
    papel = papel_na_empresa(db, usuario, empresa)
    if papel not in PAPEIS_ESCRITA:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Seu perfil é somente leitura nesta empresa.",
        )
    return usuario
