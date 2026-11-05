import uuid
from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from backend.app.core.database import Base
from backend.app.models.file import File  # 注册外键引用的文件表

class FileChunk(Base):
    """保存文件切片，后续可以用块ID关联向量记录。"""
    __tablename__ = "file_chunks"
    __table_args__ = (UniqueConstraint("file_id", "chunk_index", name="uq_file_chunks_file_index"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))  # 块唯一ID
    file_id: Mapped[str] = mapped_column(ForeignKey("files.id"), index=True)  # 所属文件
    chunk_index: Mapped[int] = mapped_column()  # 文件内的块序号
    content: Mapped[str] = mapped_column(Text)  # 本块正文
    start_offset: Mapped[int] = mapped_column()  # 原文起始位置
    end_offset: Mapped[int] = mapped_column()  # 原文结束位置
