import time
import uuid
from sqlalchemy import ForeignKey, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from backend.app.core.database import Base


class AssistantProfile(Base):
    __tablename__ = 'assistant_profiles'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    name: Mapped[str] = mapped_column(String(80))
    system_prompt: Mapped[str] = mapped_column(Text, default='')
    connection_id: Mapped[str | None] = mapped_column(ForeignKey('model_service_connections.id'), nullable=True)
    model: Mapped[str] = mapped_column(String(200), default='')
    tool_names: Mapped[list | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    knowledge_ids: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[int] = mapped_column(default=lambda: int(time.time()), onupdate=lambda: int(time.time()))


class KnowledgeGrant(Base):
    __tablename__ = 'knowledge_grants'
    knowledge_base_id: Mapped[str] = mapped_column(ForeignKey('knowledge_bases.id', ondelete='CASCADE'), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), primary_key=True)


class DocumentRevision(Base):
    __tablename__ = 'document_revisions'
    __table_args__ = (UniqueConstraint('document_id', 'revision', name='uq_document_revision'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    document_id: Mapped[str] = mapped_column(String(36), index=True)
    file_id: Mapped[str] = mapped_column(ForeignKey('files.id', ondelete='CASCADE'), index=True)
    revision: Mapped[int] = mapped_column()
    created_at: Mapped[int] = mapped_column(default=lambda: int(time.time()))
