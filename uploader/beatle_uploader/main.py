"""
Beatle Uploader: monitora diretórios locais e envia NFC-e e extratos OFX.

Dependência prevista: watchdog (Tabela 2 do TCC).
Empacotamento futuro com PyInstaller para instalação no cliente.
"""

import os


def iniciar_monitoramento(diretorio: str | None = None):
    pasta = diretorio or os.getenv("UPLOADER_WATCH_DIR", ".")
    print(f"[uploader] Monitoramento de '{pasta}' — não implementado neste protótipo.")


if __name__ == "__main__":
    iniciar_monitoramento()
