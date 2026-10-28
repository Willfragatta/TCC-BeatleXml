from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.empresa import Empresa
from app.schemas.empresa import EmpresaCreate, EmpresaResponse

router = APIRouter()


@router.get("/", response_model=list[EmpresaResponse])
def listar_empresas(db: Session = Depends(get_db)):
    return db.query(Empresa).all()


@router.post("/", response_model=EmpresaResponse, status_code=201)
def cadastrar_empresa(dados: EmpresaCreate, db: Session = Depends(get_db)):
    existente = db.query(Empresa).filter(Empresa.cnpj == dados.cnpj).first()
    if existente:
        raise HTTPException(status_code=409, detail="CNPJ já cadastrado")

    empresa = Empresa(razao_social=dados.razao_social, cnpj=dados.cnpj)
    db.add(empresa)
    db.commit()
    db.refresh(empresa)
    return empresa
