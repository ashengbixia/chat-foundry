import time
import uuid

from sqlalchemy import Boolean, ForeignKey, JSON, String, Text, UniqueConstraint, false
from sqlalchemy.orm import Mapped, mapped_column, relationship
from backend.app.models.message_file import MessageFile  # 消息与文件的关联记录
from backend.app.models.knowledge import KnowledgeBase
from backend.app.models.chat_file import ChatFile

from backend.app.core.database import Base


class Chat(Base):
    """聊天会话表，一条记录代表一个会话。"""

    __tablename__ = "chats"
    files: Mapped[list[ChatFile]] = relationship(lazy="selectin", viewonly=True, primaryjoin="and_(Chat.id == ChatFile.chat_id, ChatFile.active == True)", order_by="(ChatFile.created_at, ChatFile.file_id)")
    knowledge_bases: Mapped[list[KnowledgeBase]] = relationship(secondary="chat_knowledge_bases", lazy="selectin", viewonly=True, order_by=KnowledgeBase.name)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True, nullable=True)  # 升级前的数据在首次初始化时交给管理员

    # 自动生成唯一的会话 ID。
    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    # 会话标题。
    title: Mapped[str] = mapped_column(
        String(200),
        default="新会话",
    )
    web_search_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    title_status: Mapped[str] = mapped_column(String(16), default='manual', server_default='manual')
    kind: Mapped[str] = mapped_column(String(16), default='conversation', server_default='conversation')

    # 创建时间：Unix 秒级时间戳。
    created_at: Mapped[int] = mapped_column(
        default=lambda: int(time.time()),
    )

    # 更新时间：通过 ORM 更新这条记录时自动更新。
    updated_at: Mapped[int] = mapped_column(
        default=lambda: int(time.time()),
        onupdate=lambda: int(time.time()),
    )
class Message(Base):
    """聊天消息表，一条记录代表一条用户或助手消息。"""

    __tablename__ = "messages"
    attachments: Mapped[list[MessageFile]] = relationship(lazy="selectin", cascade="all, delete-orphan", order_by=MessageFile.position)  # 批量读取附件基本信息，不加载文件正文
    # 同一个会话内，消息序号不能重复。
    __table_args__ = (
        UniqueConstraint(
            "chat_id",
            "sequence",
            name="uq_messages_chat_sequence",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    # 这条消息属于哪个会话。
    chat_id: Mapped[str] = mapped_column(
        ForeignKey("chats.id"),
        index=True,
    )
    # 会话内的消息顺序，从 1 开始，由保存逻辑分配。
    sequence: Mapped[int] = mapped_column(nullable=False)

    # user：用户；assistant：助手。
    role: Mapped[str] = mapped_column(String(20))

    # 消息正文。
    content: Mapped[str] = mapped_column(Text, default="")

    # 结构化输出：以后保存正文、思考、工具调用等条目。
    output: Mapped[list] = mapped_column(JSON, default=list)

    # generating / completed / cancelled / failed。
    status: Mapped[str] = mapped_column(
        String(20),
        default="completed",
    )

    # 用户消息不需要模型名称，所以允许为空。
    model: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )

    # 模型返回的用量；尚未获取时为空。
    usage: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )

    # 失败时保存经过脱敏的错误说明。
    error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[int] = mapped_column(
        default=lambda: int(time.time()),
    )

    updated_at: Mapped[int] = mapped_column(
        default=lambda: int(time.time()),
        onupdate=lambda: int(time.time()),
    )
