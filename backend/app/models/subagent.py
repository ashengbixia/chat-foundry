import time
import uuid
from sqlalchemy import ForeignKey, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from backend.app.core.database import Base


class SubagentTask(Base):
    __tablename__ = 'subagent_tasks'
    __table_args__ = (UniqueConstraint('parent_message_id', 'request_key', name='uq_subagent_request'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    parent_chat_id: Mapped[str] = mapped_column(ForeignKey('chats.id', ondelete='CASCADE'), index=True)
    parent_message_id: Mapped[str] = mapped_column(ForeignKey('messages.id', ondelete='CASCADE'))
    child_chat_id: Mapped[str] = mapped_column(ForeignKey('chats.id', ondelete='CASCADE'), unique=True)
    call_id: Mapped[str] = mapped_column(String(200))
    request_key: Mapped[str] = mapped_column(String(64))
    task: Mapped[str] = mapped_column(Text)
    background: Mapped[bool] = mapped_column(default=False)
    scope: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default='queued')
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    delivery_status: Mapped[str] = mapped_column(String(20), default='none')
    created_at: Mapped[int] = mapped_column(default=lambda: int(time.time()))
    started_at: Mapped[int | None] = mapped_column(nullable=True)
    finished_at: Mapped[int | None] = mapped_column(nullable=True)
