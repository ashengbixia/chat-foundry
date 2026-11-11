import time
import uuid
from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column
from backend.app.core.database import Base

class User(Base):  # 初始管理员以外的注册账号默认只有普通用户权限
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    username: Mapped[str] = mapped_column(String(254), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="user")  # admin / user / disabled
    admin_requested: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[int] = mapped_column(default=lambda: int(time.time()))

class UserSession(Base):  # 浏览器只保存随机会话凭证，数据库只存凭证摘要
    __tablename__ = "user_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    expires_at: Mapped[int] = mapped_column(index=True)
