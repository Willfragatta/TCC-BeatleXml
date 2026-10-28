from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.documento import DocumentoFiscal
from app.schemas.documento import DocumentoCreate, DocumentoResponse

router = APIRouter()


@router.get("/", response_model=list[DocumentoResponse])
def listar_documentos(db: Session = Depends(get_db)):
    return db.query(DocumentoFiscal).all()


@router.post("/", response_model=DocumentoResponse, status_code=201)
def registrar_documento(dados: DocumentoCreate, db: Session = Depends(get_db)):
    documento = DocumentoFiscal(**dados.model_dump())
    db.add(documento)
    db.commit()
    db.refresh(documento)
    return documento
