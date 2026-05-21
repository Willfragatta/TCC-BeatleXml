from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DocumentoCreate(BaseModel):
    empresa_id: int
    tipo: str = Field(..., pattern="^(nfe|nfce|ofx|pdf)$")
    nome_arquivo: str
    origem: str = Field(default="manual", pattern="^(sefaz|uploader|manual)$")
    chave_acesso: str | None = None


class DocumentoResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    empresa_id: int
    tipo: str
    chave_acesso: str | None
    nome_arquivo: str
    origem: str
    recebido_em: datetime
