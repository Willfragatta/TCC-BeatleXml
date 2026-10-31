"""Remove o certificado A1 e apaga a conta na ordem das chaves estrangeiras."""
from sqlalchemy.orm import Session

from app.models.database import (
    AuthOtp,
    BaseConsulta,
    ContadorSyncEmpresa,
    EmailToken,
    Empresa,
    Etiqueta,
    NotaEtiqueta,
    NotaFiscal,
    ParcelaPagamento,
    ResumoPendente,
    SuporteAcao,
    SuporteLog,
    Usuario,
    UsuarioEmpresa,
)
from app.models.password_reset import PasswordReset


def _cnpj_base(cnpj: str) -> str:
    digitos = "".join(c for c in (cnpj or "") if c.isdigit())
    return digitos[:8]


def _ids(query):
    return [row[0] for row in query.all()]


def limpar_certificado(db: Session, empresa: Empresa) -> None:
    empresa.certificado_base64 = None
    empresa.certificado_senha = None
    empresa.certificado_valido_ate = None
    db.commit()


def apagar_empresa(db: Session, empresa: Empresa) -> None:
    """Apaga a empresa e o que depende dela. A base do CNPJ sai se ninguém mais a usa."""
    base = _cnpj_base(empresa.cnpj)
    ambiente = empresa.ambiente or "1"
    nota_ids = _ids(db.query(NotaFiscal.id).filter(NotaFiscal.empresa_id == empresa.id))
    if nota_ids:
        db.query(ParcelaPagamento).filter(ParcelaPagamento.nota_id.in_(nota_ids)).delete(
            synchronize_session=False
        )
        db.query(NotaEtiqueta).filter(NotaEtiqueta.nota_id.in_(nota_ids)).delete(
            synchronize_session=False
        )
        db.query(NotaFiscal).filter(NotaFiscal.id.in_(nota_ids)).delete(synchronize_session=False)

    etiqueta_ids = _ids(db.query(Etiqueta.id).filter(Etiqueta.empresa_id == empresa.id))
    if etiqueta_ids:
        db.query(NotaEtiqueta).filter(NotaEtiqueta.etiqueta_id.in_(etiqueta_ids)).delete(
            synchronize_session=False
        )
        db.query(Etiqueta).filter(Etiqueta.id.in_(etiqueta_ids)).delete(synchronize_session=False)

    db.query(ResumoPendente).filter(ResumoPendente.empresa_id == empresa.id).delete(
        synchronize_session=False
    )
    db.query(SuporteLog).filter(SuporteLog.empresa_id == empresa.id).delete(synchronize_session=False)
    db.query(SuporteAcao).filter(SuporteAcao.empresa_id == empresa.id).delete(synchronize_session=False)
    db.query(ContadorSyncEmpresa).filter(ContadorSyncEmpresa.empresa_id == empresa.id).delete(
        synchronize_session=False
    )
    db.query(UsuarioEmpresa).filter(UsuarioEmpresa.empresa_id == empresa.id).delete(
        synchronize_session=False
    )
    db.delete(empresa)
    db.flush()

    if not base:
        return
    ainda = (
        db.query(Empresa)
        .filter(Empresa.cnpj.like(f"{base}%"), Empresa.ambiente == ambiente)
        .first()
    )
    if ainda is None:
        db.query(BaseConsulta).filter(
            BaseConsulta.cnpj_base == base,
            BaseConsulta.ambiente == ambiente,
        ).delete(synchronize_session=False)


def apagar_conta(db: Session, usuario: Usuario) -> None:
    """Dono leva as empresas. Vínculo de outra pessoa leva só a conta e os laços."""
    donas = db.query(Empresa).filter(Empresa.usuario_id == usuario.id).all()
    for empresa in list(donas):
        apagar_empresa(db, empresa)

    db.query(UsuarioEmpresa).filter(UsuarioEmpresa.usuario_id == usuario.id).delete(
        synchronize_session=False
    )
    db.query(ContadorSyncEmpresa).filter(ContadorSyncEmpresa.usuario_id == usuario.id).delete(
        synchronize_session=False
    )
    etiqueta_ids = _ids(db.query(Etiqueta.id).filter(Etiqueta.usuario_id == usuario.id))
    if etiqueta_ids:
        db.query(NotaEtiqueta).filter(NotaEtiqueta.etiqueta_id.in_(etiqueta_ids)).delete(
            synchronize_session=False
        )
        db.query(Etiqueta).filter(Etiqueta.id.in_(etiqueta_ids)).delete(synchronize_session=False)

    db.query(EmailToken).filter(EmailToken.usuario_id == usuario.id).delete(synchronize_session=False)
    db.query(AuthOtp).filter(AuthOtp.usuario_id == usuario.id).delete(synchronize_session=False)
    db.query(PasswordReset).filter(PasswordReset.usuario_id == usuario.id).delete(
        synchronize_session=False
    )
    db.query(SuporteAcao).filter(SuporteAcao.usuario_suporte_id == usuario.id).delete(
        synchronize_session=False
    )
    db.delete(usuario)
    db.commit()
