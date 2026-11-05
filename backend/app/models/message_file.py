from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column
from backend.app.core.database import Base
from backend.app.models.file import File  # 注册外键所引用的文件表，独立加载消息模型时也能建表

class MessageFile(Base):
    """记录消息使用的文件，保留名称和大小快照，不重复保存正文。"""
    __tablename__ = "message_files"
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id"), primary_key=True)  # 所属消息
    file_id: Mapped[str] = mapped_column(String(36), primary_key=True, index=True)  # 原文件ID快照，文件删除后仍保留
    linked_file_id: Mapped[str | None] = mapped_column(ForeignKey("files.id", ondelete="SET NULL"), nullable=True, index=True)  # 活跃文件外键，删除文件时置空
    filename: Mapped[str] = mapped_column(String(255))  # 发送时的文件名
    size: Mapped[int] = mapped_column()  # 发送时的文件大小
    position: Mapped[int] = mapped_column(default=0)  # 附件在消息中的排列顺序

    @property
    def deleted(self) -> bool:  # 返回给前端的历史附件删除标记，不另存重复状态
        return self.linked_file_id is None
