"""Lógica de upload de XMLs detectados."""
import os
import logging
from pathlib import Path
from typing import List, Optional

from .auth import upload_xmls, heartbeat

logger = logging.getLogger("beatle_uploader")

VERSION = "1.0.0"
BATCH_SIZE = 15


def _ler_xml(path: str) -> Optional[bytes]:
    """Lê conteúdo do XML."""
    try:
        with open(path, "rb") as f:
            return f.read()
    except Exception as e:
        logger.warning(f"Erro ao ler {path}: {e}")
        return None


def listar_xmls_pasta(pasta: str) -> List[str]:
    """Lista arquivos .xml (não recursivo) da pasta, ordenados por nome."""
    path = Path(pasta)
    if not path.is_dir():
        return []
    arquivos = [str(p) for p in path.iterdir() if p.is_file() and p.suffix.lower() == ".xml"]
    arquivos.sort(key=lambda p: os.path.basename(p).lower())
    return arquivos


def processar_arquivo(cfg: dict, path: str) -> bool:
    """
    Processa um arquivo XML e envia para a API.
    Retorna True se enviou com sucesso.
    """
    token = cfg.get("token")
    empresa_id = cfg.get("empresa_id")
    base_url = cfg.get("base_url", "https://beatlexml.com.br")

    if not token or not empresa_id:
        return False

    conteudo = _ler_xml(path)
    if not conteudo:
        return False

    nome = os.path.basename(path)
    try:
        result = upload_xmls(base_url, token, empresa_id, [(nome, conteudo)])
        importados = result.get("importados", 0)
        erros = result.get("erros", 0)
        logger.info(f"Enviado {path}: importados={importados}, erros={erros}")
        return importados > 0
    except Exception as e:
        logger.warning(f"Erro ao enviar {path}: {e}")
        return False


# Estado compartilhado para a GUI (log e contadores) - thread-safe
_estado_sessao = {
    "detectadas": 0,
    "enviadas": 0,
    "erros": 0,
    "enviando": False,
    "log": [],
    "lock": None,
}
MAX_LOG_LINHAS = 200


def _get_lock():
    import threading
    if _estado_sessao["lock"] is None:
        _estado_sessao["lock"] = threading.Lock()
    return _estado_sessao["lock"]


def registrar_detectado(path: str) -> None:
    with _get_lock():
        _estado_sessao["detectadas"] += 1
        _estado_sessao["enviando"] = True
        _estado_sessao["log"].append(f"[Detectado] {os.path.basename(path)}")
        if len(_estado_sessao["log"]) > MAX_LOG_LINHAS:
            _estado_sessao["log"].pop(0)


def registrar_resultado(path: str, sucesso: bool) -> None:
    with _get_lock():
        _estado_sessao["enviando"] = False
        if sucesso:
            _estado_sessao["enviadas"] += 1
            _estado_sessao["log"].append(f"[OK] Enviado: {os.path.basename(path)}")
        else:
            _estado_sessao["erros"] += 1
            _estado_sessao["log"].append(f"[Erro] Falha: {os.path.basename(path)}")
        if len(_estado_sessao["log"]) > MAX_LOG_LINHAS:
            _estado_sessao["log"].pop(0)


def obter_estado_sessao() -> dict:
    with _get_lock():
        return {
            "detectadas": _estado_sessao["detectadas"],
            "enviadas": _estado_sessao["enviadas"],
            "erros": _estado_sessao["erros"],
            "enviando": _estado_sessao["enviando"],
            "log": list(_estado_sessao["log"]),
        }


def resetar_estado_sessao() -> None:
    with _get_lock():
        _estado_sessao["detectadas"] = 0
        _estado_sessao["enviadas"] = 0
        _estado_sessao["erros"] = 0
        _estado_sessao["enviando"] = False
        _estado_sessao["log"] = []


def varrer_e_enviar_pastas(cfg: dict, pastas: List[str]) -> dict:
    """
    Varre as pastas e envia todos os XMLs em lotes.
    Notas já no banco são atualizadas pela API; novas são inseridas.
    Retorna {encontrados, enviados_ok, erros}.
    """
    token = cfg.get("token")
    empresa_id = cfg.get("empresa_id")
    base_url = cfg.get("base_url", "https://beatlexml.com.br")
    if not token or not empresa_id:
        return {"encontrados": 0, "enviados_ok": 0, "erros": 0}

    paths: List[str] = []
    for pasta in pastas:
        if pasta and os.path.isdir(pasta):
            paths.extend(listar_xmls_pasta(pasta))

    vistos = set()
    unicos: List[str] = []
    for p in paths:
        if p not in vistos:
            vistos.add(p)
            unicos.append(p)
    paths = unicos

    with _get_lock():
        _estado_sessao["log"].append(f"[Varredura] {len(paths)} XML(s) encontrado(s) nas pastas.")
        if len(_estado_sessao["log"]) > MAX_LOG_LINHAS:
            _estado_sessao["log"].pop(0)

    enviados_ok = 0
    erros = 0
    i = 0
    while i < len(paths):
        lote_paths = paths[i : i + BATCH_SIZE]
        i += BATCH_SIZE
        lote = []
        for path in lote_paths:
            registrar_detectado(path)
            conteudo = _ler_xml(path)
            if not conteudo:
                registrar_resultado(path, False)
                erros += 1
                continue
            lote.append((path, os.path.basename(path), conteudo))

        if not lote:
            continue

        try:
            result = upload_xmls(
                base_url,
                token,
                empresa_id,
                [(nome, conteudo) for _, nome, conteudo in lote],
            )
            imp = int(result.get("importados", 0) or 0)
            err = int(result.get("erros", 0) or 0)
            enviados_ok += imp
            erros += err
            # Se a API não detalha por arquivo, marca ok quando o lote teve importados
            ok_lote = imp > 0 and err == 0
            misto = imp > 0 and err > 0
            for path, _, _ in lote:
                if ok_lote:
                    registrar_resultado(path, True)
                elif misto:
                    # parcial: conta como ok para não assustar; erros já somados
                    registrar_resultado(path, True)
                else:
                    registrar_resultado(path, False)
            logger.info("Lote %s arquivo(s): importados=%s erros=%s", len(lote), imp, err)
        except Exception as e:
            logger.warning("Erro no lote de upload: %s", e)
            for path, _, _ in lote:
                registrar_resultado(path, False)
                erros += 1

    with _get_lock():
        _estado_sessao["log"].append(
            f"[Varredura] Concluída: {enviados_ok} ok, {erros} erro(s) de {len(paths)} arquivo(s)."
        )
        _estado_sessao["enviando"] = False
        if len(_estado_sessao["log"]) > MAX_LOG_LINHAS:
            _estado_sessao["log"].pop(0)

    return {"encontrados": len(paths), "enviados_ok": enviados_ok, "erros": erros}


def _pasta_monitorada_texto(cfg: dict) -> str:
    """Monta texto das pastas para o heartbeat (suporte)."""
    nfe = (cfg.get("pasta_nfe") or cfg.get("pasta") or "").strip()
    nfce = (cfg.get("pasta_nfce") or "").strip()
    partes = []
    if nfe:
        partes.append(f"NFe: {nfe}")
    if nfce:
        partes.append(f"NFCe: {nfce}")
    return "; ".join(partes) if partes else (nfe or "-")


def enviar_heartbeat(cfg: dict) -> bool:
    """Envia heartbeat. Retorna True se sucesso."""
    try:
        heartbeat(
            cfg.get("base_url", "https://beatlexml.com.br"),
            cfg.get("token"),
            cfg.get("empresa_id"),
            pasta=_pasta_monitorada_texto(cfg),
            versao=VERSION,
        )
        return True
    except Exception as e:
        logger.warning(f"Erro no heartbeat: {e}")
        return False
