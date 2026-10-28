"""
Worker principal: sincronização periódica de NF-e recebidas via SEFAZ.

Execução prevista como serviço systemd em produção (Diagrama de Implantação).
"""


def executar_ciclo_sincronizacao():
    print("[worker] Ciclo de sincronização SEFAZ — não implementado neste protótipo.")


if __name__ == "__main__":
    executar_ciclo_sincronizacao()
