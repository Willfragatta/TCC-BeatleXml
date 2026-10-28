"""Garante uma única janela do Beatle Contador nesta máquina.

A segunda abertura pede à instância que já está na bandeja para aparecer.
"""
import socket
import sys
import threading

from beatle_contador.config import CONFIG_DIR

PORTA = 47655
_NOME_MUTEX = "Local\\BeatleContador"


class InstanciaUnica:
    def __init__(self):
        self._sock = None
        self._arquivo = None
        self._mutex = None

    def adquirir(self, ao_mostrar) -> bool:
        if not self._mutex_livre():
            self.sinalizar()
            self.liberar()
            return False
        if not self._travar_arquivo():
            self.sinalizar()
            self.liberar()
            return False
        if not self._abrir_soquete(ao_mostrar):
            self.liberar()
            return False
        return True

    def sinalizar(self) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", PORTA), timeout=1.0) as conn:
                conn.sendall(b"MOSTRAR")
            return True
        except OSError:
            return False

    def liberar(self) -> None:
        sock = self._sock
        self._sock = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        if self._arquivo is not None:
            try:
                if sys.platform == "win32":
                    import msvcrt

                    self._arquivo.seek(0)
                    msvcrt.locking(self._arquivo.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self._arquivo.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                self._arquivo.close()
            except OSError:
                pass
            self._arquivo = None
        if self._mutex is not None and sys.platform == "win32":
            import ctypes

            ctypes.windll.kernel32.CloseHandle(self._mutex)
            self._mutex = None

    def _mutex_livre(self) -> bool:
        if sys.platform != "win32":
            return True
        import ctypes

        kernel = ctypes.windll.kernel32
        self._mutex = kernel.CreateMutexW(None, False, _NOME_MUTEX)
        if not self._mutex:
            return False
        # 183 = ERROR_ALREADY_EXISTS
        if kernel.GetLastError() == 183:
            return False
        return True

    def _travar_arquivo(self) -> bool:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        caminho = CONFIG_DIR / "instancia.lock"
        self._arquivo = open(caminho, "a+", encoding="utf-8")
        try:
            if self._arquivo.tell() == 0:
                self._arquivo.write("1")
                self._arquivo.flush()
            self._arquivo.seek(0)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(self._arquivo.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._arquivo.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    def _abrir_soquete(self, ao_mostrar) -> bool:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", PORTA))
        except OSError:
            sock.close()
            self.sinalizar()
            return False
        sock.listen(1)
        sock.settimeout(1.0)
        self._sock = sock

        def ouvir():
            while self._sock is not None:
                try:
                    conn, _endereco = sock.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                try:
                    conn.recv(16)
                    if ao_mostrar:
                        ao_mostrar()
                finally:
                    try:
                        conn.close()
                    except OSError:
                        pass

        threading.Thread(target=ouvir, name="beatle-contador-instancia", daemon=True).start()
        return True
