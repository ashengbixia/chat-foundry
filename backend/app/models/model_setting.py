import time
from sqlalchemy import Float, ForeignKey, String, Text, JSON, text
from sqlalchemy.orm import Mapped, mapped_column
from backend.app.core.database import Base

class UserModelSetting(Base):  # 每个账号保存一套当前模型服务，Key只保存加密结果
    __tablename__ = "user_model_settings"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    active_connection_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    provider: Mapped[str] = mapped_column(String(20))
    base_url: Mapped[str] = mapped_column(String(1000))
    model: Mapped[str] = mapped_column(String(200))
    timeout: Mapped[float] = mapped_column(Float)
    key_source: Mapped[str] = mapped_column(String(20))  # personal / environment / none
    encrypted_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_profiles: Mapped[dict] = mapped_column(JSON, default=dict, server_default=text("'{}'"))
    updated_at: Mapped[int] = mapped_column(default=lambda: int(time.time()), onupdate=lambda: int(time.time()))


class ModelServiceConnection(Base):
    __tablename__ = 'model_service_connections'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    name: Mapped[str] = mapped_column(String(80))
    provider: Mapped[str] = mapped_column(String(20))
    base_url: Mapped[str] = mapped_column(String(1000))
    model: Mapped[str] = mapped_column(String(200))
    timeout: Mapped[float] = mapped_column(Float)
    key_source: Mapped[str] = mapped_column(String(20))
    encrypted_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_profiles: Mapped[dict] = mapped_column(JSON, default=dict, server_default=text("'{}'"))
    updated_at: Mapped[int] = mapped_column(default=lambda: int(time.time()), onupdate=lambda: int(time.time()))
