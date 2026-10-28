"""
Modelo para recuperação de senha.
"""
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Boolean
from app.models.database import Base
from datetime import datetime, timedelta

class PasswordReset(Base):
    __tablename__ = 'password_resets'
    __table_args__ = {'schema': 'nfe_app'}
    
    id = Column(Integer, primary_key=True)
    usuario_id = Column(Integer, ForeignKey('nfe_app.usuarios.id'), nullable=False)
    token = Column(String(100), unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used = Column(Boolean, default=False)
    
    def is_valid(self):
        """Verifica se o token ainda é válido."""
        return not self.used and datetime.now() < self.expires_at

