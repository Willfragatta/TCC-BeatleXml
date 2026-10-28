"""Monitoramento de pasta com watchdog."""
import os
import time
import logging
from pathlib import Path

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler, FileCreatedEvent
    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False

logger = logging.getLogger("beatle_uploader")


class XmlHandler(FileSystemEventHandler):
    """Handler para novos arquivos .xml na pasta."""

    def __init__(self, callback):
        self.callback = callback

    def on_created(self, event):
        if event.is_directory:
            return
        path = getattr(event, "dest_path", None) or event.src_path
        if path.lower().endswith(".xml"):
            time.sleep(0.5)  # Aguarda arquivo ser totalmente escrito
            if self.callback:
                self.callback(path)


def iniciar_monitor(pasta: str, callback) -> "Observer|None":
    """
    Inicia monitoramento da pasta. callback(path) é chamado quando novo .xml aparece.
    Retorna o Observer para poder parar depois.
    """
    if not WATCHDOG_AVAILABLE:
        logger.error("watchdog não instalado. Execute: pip install watchdog")
        return None

    path = Path(pasta).resolve()
    if not path.exists() or not path.is_dir():
        logger.error(f"Pasta inválida: {pasta}")
        return None

    handler = XmlHandler(callback)
    observer = Observer()
    observer.schedule(handler, str(path), recursive=False)
    observer.start()
    logger.info(f"Monitorando pasta: {path}")
    return observer
