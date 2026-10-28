from jose import JWTError, jwt
import bcrypt
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv

load_dotenv()

# Configuração
_DEFAULT_JWT = "sua-chave-secreta-super-segura-aqui-mude-em-producao"
SECRET_KEY = os.getenv("SECRET_KEY_JWT", _DEFAULT_JWT)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 30

_ENV = (os.getenv("ENV") or "").lower()
if _ENV in ("production", "prod") and (
    not os.getenv("SECRET_KEY_JWT") or SECRET_KEY == _DEFAULT_JWT
):
    raise RuntimeError(
        "SECRET_KEY_JWT obrigatória em produção (ENV=production|prod). "
        "Defina uma chave forte no .env."
    )

def criar_token(usuario_id: int) -> str:
    """Cria JWT token para sessão do usuário."""
    expire = datetime.utcnow() + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    payload = {"sub": str(usuario_id), "exp": expire, "iat": datetime.utcnow()}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def criar_token_pendente_2fa(usuario_id: int) -> str:
    expire = datetime.utcnow() + timedelta(minutes=10)
    payload = {
        "sub": str(usuario_id),
        "exp": expire,
        "iat": datetime.utcnow(),
        "purpose": "2fa",
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def verificar_token(token: str) -> int:
    """Verifica e retorna usuario_id do token. Retorna None se inválido."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("purpose"):
            return None
        usuario_id: int = int(payload.get("sub"))
        return usuario_id
    except JWTError:
        return None


def verificar_token_2fa(token: str) -> int:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("purpose") != "2fa":
            return None
        return int(payload.get("sub"))
    except JWTError:
        return None

def hash_senha(senha: str) -> str:
    """Gera hash da senha usando bcrypt."""
    # Bcrypt tem limite de 72 bytes, então truncamos se necessário
    senha_bytes = senha.encode('utf-8')
    if len(senha_bytes) > 72:
        senha_bytes = senha_bytes[:72]
    
    # Gera o salt e faz o hash
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(senha_bytes, salt)
    return hashed.decode('utf-8')

def verificar_senha(senha: str, senha_hash: str) -> bool:
    """Verifica se a senha corresponde ao hash."""
    try:
        senha_bytes = senha.encode('utf-8')
        if len(senha_bytes) > 72:
            senha_bytes = senha_bytes[:72]
        hash_bytes = senha_hash.encode('utf-8')
        return bcrypt.checkpw(senha_bytes, hash_bytes)
    except Exception:
        return False

