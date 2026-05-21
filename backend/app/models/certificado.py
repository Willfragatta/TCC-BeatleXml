from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class CertificadoDigital(Base):
    __tablename__ = "certificados_digitais"

    id: Mapped[int] = mapped_column(primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id"))
    titular: Mapped[str] = mapped_column(String(255))
    valido_ate: Mapped[datetime] = mapped_column(DateTime)
    caminho_arquivo: Mapped[str] = mapped_column(String(500))
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    empresa: Mapped["Empresa"] = relationship(back_populates="certificados")
