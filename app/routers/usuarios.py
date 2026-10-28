from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr
from typing import Optional, List
from app.models.database import Usuario, Empresa, UsuarioEmpresa
from app.core.dependencies import (
    get_db,
    get_usuario_atual,
    get_empresa_ativa,
    ids_empresas_acessiveis,
    papel_na_empresa,
    exige_papel_master,
)
from app.core.auth import hash_senha
from app.core.paginas import PAGINAS_OPCOES
from datetime import datetime

router = APIRouter()

PAPEIS = ("empresa_master", "operador", "leitura")


class UsuarioCreate(BaseModel):
    email: EmailStr
    nome_completo: str
    senha: str
    telefone: Optional[str] = None
    papel: Optional[str] = "operador"
    empresas_ids: Optional[List[int]] = None
    paginas_permitidas: Optional[List[str]] = None
    role: Optional[str] = None  # admin pode promover suporte


class UsuarioUpdate(BaseModel):
    nome_completo: Optional[str] = None
    telefone: Optional[str] = None
    status: Optional[str] = None
    papel: Optional[str] = None
    empresas_ids: Optional[List[int]] = None
    paginas_permitidas: Optional[List[str]] = None
    role: Optional[str] = None
    is_admin: Optional[bool] = None
    senha: Optional[str] = None


class UsuarioResponse(BaseModel):
    id: int
    email: str
    nome_completo: Optional[str]
    telefone: Optional[str]
    status: str
    created_at: datetime
    last_login: Optional[datetime]
    total_empresas: int
    role: Optional[str] = None
    is_admin: Optional[bool] = None
    vinculos: Optional[List[dict]] = None

    class Config:
        from_attributes = True


def _is_admin_or_suporte(usuario: Usuario) -> bool:
    return bool(
        getattr(usuario, "role", None) == "suporte"
        or getattr(usuario, "is_admin", False)
    )


def _empresas_gerenciaveis(db: Session, usuario: Usuario) -> List[int]:
    if _is_admin_or_suporte(usuario):
        return [e.id for e in db.query(Empresa).all()]
    ids = []
    for eid in ids_empresas_acessiveis(db, usuario):
        emp = db.query(Empresa).filter_by(id=eid).first()
        if emp and papel_na_empresa(db, usuario, emp) == "empresa_master":
            ids.append(eid)
    return ids


def _serializar(db: Session, u: Usuario) -> dict:
    vinculos = (
        db.query(UsuarioEmpresa)
        .filter_by(usuario_id=u.id)
        .all()
    )
    total = len(vinculos)
    if total == 0:
        total = db.query(Empresa).filter_by(usuario_id=u.id).count()
    return {
        "id": u.id,
        "email": u.email,
        "nome_completo": u.nome_completo,
        "telefone": u.telefone,
        "status": u.status,
        "created_at": u.created_at,
        "last_login": u.last_login,
        "total_empresas": total,
        "role": getattr(u, "role", None),
        "is_admin": bool(getattr(u, "is_admin", False)),
        "vinculos": [
            {
                "empresa_id": v.empresa_id,
                "papel": v.papel or "operador",
                "paginas_permitidas": v.paginas_permitidas,
            }
            for v in vinculos
        ],
    }


def _aplicar_vinculos(
    db: Session,
    alvo: Usuario,
    empresas_ids: List[int],
    papel: str,
    paginas: Optional[List[str]],
    permitidas: List[int],
):
    papel = papel if papel in PAPEIS else "operador"
    for eid in empresas_ids:
        if eid not in permitidas:
            raise HTTPException(status_code=403, detail=f"Sem permissão na empresa {eid}")
    # Remove vínculos antigos só nas empresas gerenciáveis
    existentes = (
        db.query(UsuarioEmpresa)
        .filter(
            UsuarioEmpresa.usuario_id == alvo.id,
            UsuarioEmpresa.empresa_id.in_(permitidas),
        )
        .all()
    )
    existentes_map = {v.empresa_id: v for v in existentes}
    desejados = set(empresas_ids)
    for eid, vinculo in list(existentes_map.items()):
        if eid not in desejados:
            db.delete(vinculo)
    for eid in desejados:
        vinculo = existentes_map.get(eid)
        if vinculo is None:
            vinculo = UsuarioEmpresa(
                usuario_id=alvo.id,
                empresa_id=eid,
                papel=papel,
                paginas_permitidas=paginas,
                created_at=datetime.now(),
            )
            db.add(vinculo)
        else:
            vinculo.papel = papel
            vinculo.paginas_permitidas = paginas


@router.get("/meta")
async def meta_usuarios(
    usuario=Depends(get_usuario_atual),
    empresa=Depends(get_empresa_ativa),
    db: Session = Depends(get_db),
):
    gerenciaveis = _empresas_gerenciaveis(db, usuario)
    empresas = db.query(Empresa).filter(Empresa.id.in_(gerenciaveis)).all() if gerenciaveis else []
    return {
        "papel_efetivo": papel_na_empresa(db, usuario, empresa),
        "pode_gerenciar": bool(gerenciaveis) or _is_admin_or_suporte(usuario),
        "papeis": list(PAPEIS),
        "paginas": PAGINAS_OPCOES,
        "empresas": [
            {"id": e.id, "razao_social": e.razao_social, "cnpj": e.cnpj}
            for e in empresas
        ],
        "is_admin": bool(getattr(usuario, "is_admin", False)),
    }


@router.get("/", response_model=List[UsuarioResponse])
async def listar_usuarios(
    usuario=Depends(get_usuario_atual),
    db: Session = Depends(get_db),
):
    if _is_admin_or_suporte(usuario):
        usuarios = db.query(Usuario).order_by(Usuario.id).all()
    else:
        gerenciaveis = _empresas_gerenciaveis(db, usuario)
        ids_u = {usuario.id}
        if gerenciaveis:
            for (uid,) in db.query(UsuarioEmpresa.usuario_id).filter(
                UsuarioEmpresa.empresa_id.in_(gerenciaveis)
            ).all():
                ids_u.add(uid)
            for (uid,) in db.query(Empresa.usuario_id).filter(
                Empresa.id.in_(gerenciaveis)
            ).all():
                if uid:
                    ids_u.add(uid)
        # Também donos das empresas do usuário (legado)
        for eid in ids_empresas_acessiveis(db, usuario):
            for (uid,) in db.query(UsuarioEmpresa.usuario_id).filter_by(empresa_id=eid).all():
                ids_u.add(uid)
        usuarios = db.query(Usuario).filter(Usuario.id.in_(ids_u)).all()

    return [_serializar(db, u) for u in usuarios]


@router.post("/", response_model=UsuarioResponse, status_code=201)
async def criar_usuario(
    request: Request,
    usuario_data: UsuarioCreate,
    usuario_atual: Usuario = Depends(exige_papel_master),
    db: Session = Depends(get_db),
):
    if db.query(Usuario).filter_by(email=usuario_data.email).first():
        raise HTTPException(status_code=400, detail="Email já cadastrado")

    novo = Usuario(
        email=usuario_data.email,
        senha_hash=hash_senha(usuario_data.senha),
        nome_completo=usuario_data.nome_completo,
        telefone=usuario_data.telefone,
        status="ativo",
        role="cliente",
        created_at=datetime.now(),
    )
    if _is_admin_or_suporte(usuario_atual) and usuario_data.role in ("suporte", "cliente", "admin"):
        novo.role = usuario_data.role

    db.add(novo)
    db.flush()

    permitidas = _empresas_gerenciaveis(db, usuario_atual)
    empresas_ids = usuario_data.empresas_ids
    if not empresas_ids:
        try:
            emp = get_empresa_ativa(request, usuario_atual, db)
            empresas_ids = [emp.id] if emp.id in permitidas else (permitidas[:1] or [])
        except Exception:
            empresas_ids = permitidas[:1] or []
    if empresas_ids:
        _aplicar_vinculos(
            db,
            novo,
            empresas_ids,
            usuario_data.papel or "operador",
            usuario_data.paginas_permitidas,
            permitidas,
        )
    db.commit()
    db.refresh(novo)
    return _serializar(db, novo)


@router.put("/{usuario_id}", response_model=UsuarioResponse)
async def editar_usuario(
    usuario_id: int,
    usuario_data: UsuarioUpdate,
    usuario_atual: Usuario = Depends(exige_papel_master),
    db: Session = Depends(get_db),
):
    permitidas = _empresas_gerenciaveis(db, usuario_atual)
    alvo = db.query(Usuario).filter_by(id=usuario_id).first()
    if not alvo:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")

    if not _is_admin_or_suporte(usuario_atual):
        vinculos_alvo = [
            v.empresa_id
            for v in db.query(UsuarioEmpresa).filter_by(usuario_id=alvo.id).all()
        ]
        if not set(vinculos_alvo) & set(permitidas) and alvo.id != usuario_atual.id:
            # permite se for dono de alguma empresa gerenciável
            dono = db.query(Empresa).filter(
                Empresa.usuario_id == alvo.id, Empresa.id.in_(permitidas)
            ).first()
            if not dono:
                raise HTTPException(status_code=403, detail="Sem permissão para editar este usuário")

    if usuario_data.nome_completo is not None:
        alvo.nome_completo = usuario_data.nome_completo
    if usuario_data.telefone is not None:
        alvo.telefone = usuario_data.telefone
    if usuario_data.status is not None:
        alvo.status = usuario_data.status
    if usuario_data.senha:
        alvo.senha_hash = hash_senha(usuario_data.senha)

    if getattr(usuario_atual, "is_admin", False):
        if usuario_data.role is not None:
            alvo.role = usuario_data.role
        if usuario_data.is_admin is not None:
            alvo.is_admin = bool(usuario_data.is_admin)

    if usuario_data.empresas_ids is not None or usuario_data.papel is not None:
        empresas_ids = usuario_data.empresas_ids
        if empresas_ids is None:
            empresas_ids = [
                v.empresa_id
                for v in db.query(UsuarioEmpresa).filter_by(usuario_id=alvo.id).all()
                if v.empresa_id in permitidas
            ]
        papel = usuario_data.papel or "operador"
        _aplicar_vinculos(
            db,
            alvo,
            empresas_ids,
            papel,
            usuario_data.paginas_permitidas,
            permitidas,
        )
    elif usuario_data.paginas_permitidas is not None:
        for v in db.query(UsuarioEmpresa).filter(
            UsuarioEmpresa.usuario_id == alvo.id,
            UsuarioEmpresa.empresa_id.in_(permitidas),
        ).all():
            v.paginas_permitidas = usuario_data.paginas_permitidas

    db.commit()
    db.refresh(alvo)
    return _serializar(db, alvo)


@router.delete("/{usuario_id}", status_code=204)
async def deletar_usuario(
    usuario_id: int,
    usuario_atual: Usuario = Depends(exige_papel_master),
    db: Session = Depends(get_db),
):
    if usuario_id == usuario_atual.id:
        raise HTTPException(status_code=400, detail="Não é possível deletar seu próprio usuário")

    alvo = db.query(Usuario).filter_by(id=usuario_id).first()
    if not alvo:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")

    if not _is_admin_or_suporte(usuario_atual):
        permitidas = _empresas_gerenciaveis(db, usuario_atual)
        vinculos = db.query(UsuarioEmpresa).filter_by(usuario_id=alvo.id).all()
        if not any(v.empresa_id in permitidas for v in vinculos):
            raise HTTPException(status_code=403, detail="Sem permissão para deletar este usuário")
        # Remove só vínculos das empresas gerenciáveis
        for v in vinculos:
            if v.empresa_id in permitidas:
                db.delete(v)
        db.commit()
        # Se não restou vínculo nem é dono, remove usuário
        ainda = db.query(UsuarioEmpresa).filter_by(usuario_id=alvo.id).count()
        dono = db.query(Empresa).filter_by(usuario_id=alvo.id).count()
        if ainda or dono:
            return None

    db.query(UsuarioEmpresa).filter_by(usuario_id=alvo.id).delete(synchronize_session=False)
    db.delete(alvo)
    db.commit()
    return None
