from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Empresa(Base):
    __tablename__ = "empresas"

    id: Mapped[int] = mapped_column(primary_key=True)
    razao_social: Mapped[str] = mapped_column(String(255))
    cnpj: Mapped[str] = mapped_column(String(14), unique=True, index=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    usuarios: Mapped[list["Usuario"]] = relationship(back_populates="empresa")
    certificados: Mapped[list["CertificadoDigital"]] = relationship(back_populates="empresa")
    documentos: Mapped[list["DocumentoFiscal"]] = relationship(back_populates="empresa")
