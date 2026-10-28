from cryptography.fernet import Fernet
import os
from dotenv import load_dotenv

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY_CERTIFICADOS")

# Só inicializa a suite se a chave existir
if not SECRET_KEY:
    raise ValueError("ERRO: SECRET_KEY_CERTIFICADOS não encontrada no arquivo .env")

try:
    cipher_suite = Fernet(SECRET_KEY.encode())
except Exception as e:
    raise ValueError(f"ERRO: A chave secreta no .env é inválida: {e}")

def criptografar(texto: str) -> str:
    if not texto: return ""
    return cipher_suite.encrypt(texto.encode()).decode()

def descriptografar(texto_cripto: str) -> str:
    if not texto_cripto: 
        return ""
    try:
        # Tenta descriptografar
        texto_bytes = cipher_suite.decrypt(texto_cripto.encode())
        return texto_bytes.decode('utf-8')
    except Exception as e:
        # Se falhar, levanta exceção com detalhes
        raise ValueError(f"Erro ao descriptografar: {str(e)} | Tipo: {type(e).__name__}")