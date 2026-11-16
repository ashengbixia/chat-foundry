"""账号级长期记忆与工具权限；不与会话摘要混用。"""
import time
import uuid
from sqlalchemy import ForeignKey, String, Text, Boolean, false, true
from sqlalchemy.orm import Mapped, mapped_column
from backend.app.core.database import Base


class UserToolSetting(Base):
    __tablename__ = 'user_tool_settings'
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), primary_key=True)
    memory_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    history_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    knowledge_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    follow_ups_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())


class Memory(Base):
    __tablename__ = 'memories'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    content: Mapped[str] = mapped_column(Text)
    path: Mapped[str] = mapped_column(String(300), default='/')
    type: Mapped[str] = mapped_column(String(16), default='user')
    revision: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[int] = mapped_column(default=lambda: int(time.time()))
    updated_at: Mapped[int] = mapped_column(default=lambda: int(time.time()))
