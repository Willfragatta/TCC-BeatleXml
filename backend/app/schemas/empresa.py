from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class EmpresaCreate(BaseModel):
    razao_social: str = Field(..., min_length=2, max_length=255)
    cnpj: str = Field(..., min_length=14, max_length=14)


class EmpresaResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    razao_social: str
    cnpj: str
    criado_em: datetime
