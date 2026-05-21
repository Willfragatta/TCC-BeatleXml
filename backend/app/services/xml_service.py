"""
Serviço de leitura e validação de arquivos XML fiscais.

Implementação prevista com lxml e signxml conforme Tabela 2 do TCC.
"""


class XmlService:
    def extrair_chave_acesso(self, conteudo_xml: bytes) -> str | None:
        # TODO: parsear XML de NF-e/NFC-e e retornar chave de 44 dígitos
        return None
