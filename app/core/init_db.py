import sys
import os

# Adiciona a raiz do projeto ao path para o Python achar os módulos
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from app.models.database import Base, engine
from app.models.password_reset import PasswordReset  # registra a tabela no create_all
from sqlalchemy import text

def inicializar_banco():
    try:
        with engine.connect() as conn:
            # Garante que o schema existe
            conn.execute(text("CREATE SCHEMA IF NOT EXISTS nfe_app"))
            conn.commit()
            print("Schema nfe_app verificado/criado.")

        Base.metadata.create_all(bind=engine)
        print("Tabelas sincronizadas com sucesso.")

    except Exception as e:
        print(f"Erro ao inicializar banco: {e}")
        raise

if __name__ == "__main__":
    inicializar_banco()