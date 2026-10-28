"""Grava parcelas lidas do XML da nota. Usado na importação e na carga de demonstração."""
from app.models.database import ParcelaPagamento
from app.services.parser import extrair_pagamento_xml


def gravar_parcelas(db, nota) -> int:
    if not getattr(nota, "xml_conteudo", None):
        return 0
    pay = extrair_pagamento_xml(nota.xml_conteudo)
    db.query(ParcelaPagamento).filter(ParcelaPagamento.nota_id == nota.id).delete()
    parcelas = pay.get("parcelas") or []
    for item in parcelas:
        db.add(
            ParcelaPagamento(
                nota_id=nota.id,
                numero_parcela=item.get("numero_parcela"),
                data_vencimento=item.get("data_vencimento"),
                valor=item.get("valor", 0),
                forma_pagamento=item.get("forma_pagamento"),
                forma_pagamento_label=item.get("forma_pagamento_label"),
                ind_pag=item.get("ind_pag"),
                data_pagamento=item.get("data_pagamento"),
                origem_nfe=bool(item.get("origem_nfe", True)),
                editavel=bool(item.get("editavel", False)),
            )
        )
    return len(parcelas)
