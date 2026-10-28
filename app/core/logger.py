import logging
import os
import re
from logging.handlers import RotatingFileHandler

# Handler de arquivo por empresa (adicionado/removido dinamicamente no worker)
_empresa_handler = None

def _cnpj_14(cnpj):
    """Retorna CNPJ com 14 dígitos (apenas números)."""
    if not cnpj:
        return "00000000000000"
    digits = re.sub(r"\D", "", str(cnpj))
    return (digits[:14]).zfill(14) if len(digits) >= 14 else digits.zfill(14)

def get_empresa_file_handler(empresa_id, cnpj):
    """Cria um RotatingFileHandler para logs da empresa. Não adiciona ao logger."""
    os.makedirs("logs", exist_ok=True)
    cnpj_14 = _cnpj_14(cnpj)
    log_file = os.path.join("logs", f"{empresa_id}_{cnpj_14}.log")
    handler = RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
    )
    handler.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    return handler

def set_empresa_log_context(empresa_id, cnpj):
    """Adiciona handler de arquivo por empresa ao logger beatle_xml."""
    global _empresa_handler
    clear_empresa_log_context()
    _empresa_handler = get_empresa_file_handler(empresa_id, cnpj)
    logger = logging.getLogger("beatle_xml")
    logger.addHandler(_empresa_handler)

def clear_empresa_log_context():
    """Remove o handler de arquivo por empresa do logger beatle_xml."""
    global _empresa_handler
    if _empresa_handler is None:
        return
    logger = logging.getLogger("beatle_xml")
    try:
        logger.removeHandler(_empresa_handler)
    except Exception:
        pass
    try:
        _empresa_handler.close()
    except Exception:
        pass
    _empresa_handler = None

def setup_logger(name="beatle_xml", log_file="logs/beatle_xml.log"):
    """Configura logger estruturado para auditoria."""
    # Cria diretório de logs se não existir
    os.makedirs("logs", exist_ok=True)
    
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    
    # Evita duplicação de handlers
    if logger.handlers:
        return logger
    
    # Handler para arquivo com rotação
    file_handler = RotatingFileHandler(
        log_file, 
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5
    )
    file_handler.setLevel(logging.INFO)
    
    # Handler para console
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    
    # Formato estruturado
    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

