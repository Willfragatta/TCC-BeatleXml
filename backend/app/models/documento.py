from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class DocumentoFiscal(Base):
    __tablename__ = "documentos_fiscais"

    id: Mapped[int] = mapped_column(primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id"))
    tipo: Mapped[str] = mapped_column(String(10))  # nfe | nfce | ofx | pdf
    chave_acesso: Mapped[str | None] = mapped_column(String(44), nullable=True)
    nome_arquivo: Mapped[str] = mapped_column(String(255))
    origem: Mapped[str] = mapped_column(String(20))  # sefaz | uploader | manual
    recebido_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    empresa: Mapped["Empresa"] = relationship(back_populates="documentos")
