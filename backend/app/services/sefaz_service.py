"""
Serviço de integração com a Distribuição DF-e da SEFAZ.

Implementação prevista na fase de Desenvolvimento e Programação:
consulta periódica via certificado digital, assinatura XML e tratamento
de respostas (incluindo consumo indevido).
"""


class SefazService:
    def consultar_nfe_recebidas(self, empresa_id: int) -> list[dict]:
        # TODO: integrar com worker e certificado digital da empresa
        return []
