from sqlalchemy import (
    create_engine, Column, Integer, String, Text, Numeric, DateTime,
    ForeignKey, Boolean, UniqueConstraint, Index,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime
import os
import urllib.parse  # Biblioteca para limpar caracteres especiais
from dotenv import load_dotenv

load_dotenv()

Base = declarative_base()

# --- Definição das Tabelas (Mantenha como você já tem) ---

class Usuario(Base):
    __tablename__ = 'usuarios'
    __table_args__ = {'schema': 'nfe_app'}
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False)
    senha_hash = Column(Text, nullable=False)
    status_assinatura = Column(String, default='teste')
    # Novos campos para MVP
    nome_completo = Column(String(200))
    telefone = Column(String(20))
    status = Column(String(20), default='ativo')  # pendente, ativo, suspenso, cancelado
    created_at = Column(DateTime, default=datetime.now)
    last_login = Column(DateTime, nullable=True)
    status_pagamento = Column(String(20), default='pendente')  # pendente, pago, vencido
    data_vencimento = Column(DateTime, nullable=True)
    data_pagamento = Column(DateTime, nullable=True)
    is_admin = Column(Boolean, default=False, nullable=True)  # Flag para administrador do sistema
    role = Column(String(20), default='cliente')  # cliente, contador, suporte, admin
    email_confirmado_em = Column(DateTime, nullable=True)
    dois_fatores_email = Column(Boolean, default=False, nullable=False)
    contador_agente_ativo = Column(Boolean, default=False)  # Beatle Contador: buscas ligadas
    contador_agente_pausado_em = Column(DateTime, nullable=True)
    contador_agente_retomar_em = Column(DateTime, nullable=True)


class ContadorSyncEmpresa(Base):
    """Pause/retoma sync por empresa no Beatle Contador."""
    __tablename__ = "contador_sync_empresas"
    __table_args__ = (
        UniqueConstraint("usuario_id", "empresa_id", name="uq_contador_sync_usuario_empresa"),
        {"schema": "nfe_app"},
    )
    id = Column(Integer, primary_key=True)
    usuario_id = Column(Integer, ForeignKey("nfe_app.usuarios.id"), nullable=False)
    empresa_id = Column(Integer, ForeignKey("nfe_app.empresas.id"), nullable=False)
    ativo = Column(Boolean, default=True, nullable=False)
    pausado_em = Column(DateTime, nullable=True)
    retomar_em = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class Empresa(Base):
    __tablename__ = 'empresas'
    __table_args__ = {'schema': 'nfe_app'}
    id = Column(Integer, primary_key=True)
    usuario_id = Column(Integer, ForeignKey('nfe_app.usuarios.id'))
    cnpj = Column(String(14), unique=True, nullable=False)
    razao_social = Column(Text)
    certificado_base64 = Column(Text) 
    certificado_senha = Column(Text)  
    ultimo_nsu = Column(String(15), default='000000000000000')  # Padrão SEFAZ: 15 dígitos zerados
    pasta_destino = Column(Text)
    ultima_consulta = Column(DateTime, nullable=True)
    # Novos campos para controle de ambiente e bloqueios
    ambiente = Column(String(1), default='1')  # 1=Produção, 2=Homologação
    uf_autor = Column(String(2), default='41')  # Código da UF (41=PR)
    bloqueio_656_ate = Column(DateTime, nullable=True)  # Timestamp até quando está bloqueado por 656
    max_nsu_conhecido = Column(String(15), nullable=True)  # Último maxNSU conhecido
    status = Column(String(20), default='pendente_aprovacao')  # pendente_aprovacao, aprovado, rejeitado
    data_aprovacao = Column(DateTime, nullable=True)  # Data de aprovação
    data_ativacao = Column(DateTime, nullable=True)  # Data de ativação (para controle de 1 mês)
    proxima_consulta_periodica = Column(DateTime, nullable=True)  # Próxima consulta periódica permitida (após fim da fila)
    ultima_ciencia_enviada = Column(DateTime, nullable=True)  # Timestamp da última ciência da operação enviada
    bloqueio_manual_ate = Column(DateTime, nullable=True)  # Bloqueio aplicado pelo suporte
    ultima_sync_mensagem = Column(String(255), nullable=True)  # Resultado da última sync: "Concluída", "Bloqueio 656 - aguardar 2h", etc.
    uploader_ultimo_heartbeat = Column(DateTime, nullable=True)
    uploader_pasta_monitorada = Column(String(500), nullable=True)
    uploader_versao = Column(String(20), nullable=True)
    contador_ultimo_heartbeat = Column(DateTime, nullable=True)
    contador_versao = Column(String(20), nullable=True)
    ultima_sync_nfe_qtd = Column(Integer, nullable=True)
    ultima_sync_cstat = Column(String(10), nullable=True)
    certificado_valido_ate = Column(DateTime, nullable=True)

class UsuarioEmpresa(Base):
    __tablename__ = 'usuarios_empresas'
    __table_args__ = {'schema': 'nfe_app'}
    id = Column(Integer, primary_key=True)
    usuario_id = Column(Integer, ForeignKey('nfe_app.usuarios.id'), nullable=False)
    empresa_id = Column(Integer, ForeignKey('nfe_app.empresas.id'), nullable=False)
    # empresa_master | operador | leitura
    papel = Column(String(30), default='operador')
    # lista de paths liberados, ex.: ["/notas","/financeiro"]; null = padrao do papel
    paginas_permitidas = Column(JSONB, nullable=True)
    created_at = Column(DateTime, default=datetime.now)

class SuporteLog(Base):
    __tablename__ = 'suporte_logs'
    __table_args__ = {'schema': 'nfe_app'}
    id = Column(Integer, primary_key=True)
    empresa_id = Column(Integer, ForeignKey('nfe_app.empresas.id'), nullable=True)
    nivel = Column(String(10), default='INFO')  # INFO, WARNING, ERROR
    tipo = Column(String(50), default='sistema')  # sistema, sefaz, certificado, nsu, etc.
    mensagem = Column(Text, nullable=False)
    cstat = Column(String(10), nullable=True)
    ult_nsu = Column(String(15), nullable=True)
    max_nsu = Column(String(15), nullable=True)
    contexto = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now)

class SuporteAcao(Base):
    __tablename__ = 'suporte_acoes'
    __table_args__ = {'schema': 'nfe_app'}
    id = Column(Integer, primary_key=True)
    empresa_id = Column(Integer, ForeignKey('nfe_app.empresas.id'), nullable=True)
    usuario_suporte_id = Column(Integer, ForeignKey('nfe_app.usuarios.id'), nullable=False)
    acao = Column(String(50), nullable=False)
    payload = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now)

class BaseConsulta(Base):
    __tablename__ = 'bases_consulta'
    __table_args__ = (
        UniqueConstraint('cnpj_base', 'ambiente', name='uq_bases_consulta_cnpj_base_ambiente'),
        {'schema': 'nfe_app'}
    )
    id = Column(Integer, primary_key=True)
    cnpj_base = Column(String(8), nullable=False)
    ambiente = Column(String(1), default='1')
    bloqueio_656_ate = Column(DateTime, nullable=True)
    processamento_ate = Column(DateTime, nullable=True)
    proxima_consulta_periodica = Column(DateTime, nullable=True)
    ultima_consulta = Column(DateTime, nullable=True)
    max_nsu_cache = Column(String(15), nullable=True)
    max_nsu_cache_cnpj = Column(String(14), nullable=True)
    max_nsu_cache_expira_em = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

class NotaFiscal(Base):
    __tablename__ = 'notas_fiscais'
    __table_args__ = {'schema': 'nfe_app'}
    id = Column(Integer, primary_key=True)
    empresa_id = Column(Integer, ForeignKey('nfe_app.empresas.id'))
    chave_acesso = Column(String(44), unique=True)
    numero_nfe = Column(String(20))
    emitente_nome = Column(Text)
    emitente_cnpj = Column(String(14), nullable=True)
    natureza_operacao = Column(Text, nullable=True)
    valor_total = Column(Numeric(15, 2))
    data_emissao = Column(DateTime)
    status_manifesto = Column(String(50))
    tags = Column(ARRAY(String))
    xml_conteudo = Column(Text)
    origem = Column(String(20), default='RECEBIDA')  # RECEBIDA (SEFAZ) ou EMITIDA (uploader)
    modelo = Column(String(2), default='55')  # 55=NFe, 65=NFCe
    serie = Column(String(10), nullable=True)  # Série do documento (ide/serie)


class ParcelaPagamento(Base):
    """Parcelas/obrigações financeiras extraídas da NFe (cobr/dup, pag/detPag) ou linha sintética para nota sem cobr."""
    __tablename__ = 'parcelas_pagamento'
    __table_args__ = {'schema': 'nfe_app'}
    id = Column(Integer, primary_key=True)
    nota_id = Column(Integer, ForeignKey('nfe_app.notas_fiscais.id'), nullable=False)
    numero_parcela = Column(String(20), nullable=True)  # ex. "1/3" ou nDup
    data_vencimento = Column(DateTime, nullable=True)
    valor = Column(Numeric(15, 2), nullable=False)
    forma_pagamento = Column(String(5), nullable=True)  # código tPag (01, 15, 17, 99, etc.)
    forma_pagamento_label = Column(String(50), nullable=True)  # ex. "Boleto", "PIX"
    ind_pag = Column(String(1), nullable=True)  # 0=à vista, 1=à prazo
    data_pagamento = Column(DateTime, nullable=True)  # preenchido pela NFe (dPag) ou pelo usuário
    origem_nfe = Column(Boolean, default=True)  # True se veio do XML
    editavel = Column(Boolean, default=False)  # True só quando a NFe não informou data de pagamento
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class Etiqueta(Base):
    """Etiquetas personalizadas por usuário e empresa."""
    __tablename__ = 'etiquetas'
    __table_args__ = {'schema': 'nfe_app'}
    id = Column(Integer, primary_key=True)
    usuario_id = Column(Integer, ForeignKey('nfe_app.usuarios.id'), nullable=False)
    empresa_id = Column(Integer, ForeignKey('nfe_app.empresas.id'), nullable=True)  # NULL = todas empresas
    nome = Column(String(100), nullable=False)
    cor = Column(String(7), default='#3B82F6')  # Borda (#RRGGBB) — legado
    cor_fundo = Column(String(7), nullable=True)  # Fundo (#RRGGBB)
    cor_texto = Column(String(7), nullable=True)  # Fonte (#RRGGBB)
    created_at = Column(DateTime, default=datetime.now)

class NotaEtiqueta(Base):
    """Relação many-to-many entre notas e etiquetas."""
    __tablename__ = 'nota_etiquetas'
    __table_args__ = {'schema': 'nfe_app'}
    id = Column(Integer, primary_key=True)
    nota_id = Column(Integer, ForeignKey('nfe_app.notas_fiscais.id'), nullable=False)
    etiqueta_id = Column(Integer, ForeignKey('nfe_app.etiquetas.id'), nullable=False)
    created_at = Column(DateTime, default=datetime.now)


class ResumoPendente(Base):
    """Resumos que precisam de ciência da operação para liberar XML completo."""
    __tablename__ = 'resumos_pendentes'
    __table_args__ = {'schema': 'nfe_app'}
    id = Column(Integer, primary_key=True)
    empresa_id = Column(Integer, ForeignKey('nfe_app.empresas.id'), nullable=False)
    chave_acesso = Column(String(44), nullable=False)
    data_ciencia_enviada = Column(DateTime, nullable=True)  # Quando a ciência foi enviada
    data_proxima_tentativa = Column(DateTime, nullable=False)  # Quando tentar buscar XML completo novamente
    tentativas = Column(Integer, default=0)  # Número de tentativas de buscar XML completo
    status = Column(String(20), default='pendente')  # pendente, processando, completo, erro
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

class AuthTentativa(Base):
    """Falhas de login/cadastro/reset para rate limit entre workers."""
    __tablename__ = 'auth_tentativas'
    __table_args__ = (
        Index('ix_auth_tentativas_ip_created', 'ip', 'created_at'),
        Index('ix_auth_tentativas_email_created', 'email', 'created_at'),
        {'schema': 'nfe_app'},
    )
    id = Column(Integer, primary_key=True)
    ip = Column(String(45), nullable=False)
    email = Column(String(255), nullable=True)
    acao = Column(String(30), nullable=False)
    created_at = Column(DateTime, default=datetime.now)


class EmailToken(Base):
    """Token de confirmação de e-mail (cadastro)."""
    __tablename__ = 'email_tokens'
    __table_args__ = {'schema': 'nfe_app'}
    id = Column(Integer, primary_key=True)
    usuario_id = Column(Integer, ForeignKey('nfe_app.usuarios.id'), nullable=False)
    token = Column(String(100), unique=True, nullable=False)
    tipo = Column(String(20), nullable=False, default='confirmar')
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used = Column(Boolean, default=False)


class AuthOtp(Base):
    """Código de 2FA por e-mail."""
    __tablename__ = 'auth_otp'
    __table_args__ = {'schema': 'nfe_app'}
    id = Column(Integer, primary_key=True)
    usuario_id = Column(Integer, ForeignKey('nfe_app.usuarios.id'), nullable=False)
    codigo_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    tentativas = Column(Integer, default=0, nullable=False)
    used = Column(Boolean, default=False, nullable=False)


# --- CONFIGURAÇÃO DA CONEXÃO (O PULO DO GATO) ---

def get_engine():
    raw_url = os.getenv("DATABASE_URL")
    # O requirements traz o psycopg2. Sem o driver no endereço, o SQLAlchemy 2.1
    # procura o pacote psycopg (versão 3), que não está instalado.
    if raw_url and raw_url.startswith("postgresql://"):
        raw_url = "postgresql+psycopg2://" + raw_url[len("postgresql://"):]
    elif raw_url and raw_url.startswith("postgres://"):
        raw_url = "postgresql+psycopg2://" + raw_url[len("postgres://"):]
    pool = dict(
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        pool_recycle=1800,
    )
    # Postgres em latin-1 no client quebra XML/nomes com travessao Unicode (U+2013).
    connect_args = {"client_encoding": "utf8"}

    # Se a senha tiver caracteres como # ou @, precisamos fazer o escape
    # Esta lógica reconstrói a URL codificando apenas a parte da senha
    if raw_url and "@" in raw_url:
        prefix, rest = raw_url.split("://")
        user_pass, host_db = rest.rsplit("@", 1)
        user, password = user_pass.split(":", 1)

        # Codifica caracteres especiais da senha (# vira %23, @ vira %40)
        password_encoded = urllib.parse.quote_plus(password)

        final_url = f"{prefix}://{user}:{password_encoded}@{host_db}"
        return create_engine(final_url, connect_args=connect_args, **pool)

    return create_engine(raw_url, connect_args=connect_args, **pool)

engine = get_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)