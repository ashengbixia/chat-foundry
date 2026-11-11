import time
from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column
from backend.app.core.database import Base


class ChatFile(Base):  # 会话资料与消息附件快照分开；移除后不会被历史消息自动加回来
    __tablename__ = "chat_files"
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), primary_key=True)
    file_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    linked_file_id: Mapped[str | None] = mapped_column(ForeignKey("files.id", ondelete="SET NULL"), nullable=True, index=True)
    filename: Mapped[str] = mapped_column(String(255))
    size: Mapped[int] = mapped_column()
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[int] = mapped_column(default=lambda: int(time.time()))

    @property
    def deleted(self) -> bool:
        return self.linked_file_id is None
