from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column
from backend.app.core.database import Base


class FileCleanup(Base):  # 文件行删除后仍存在，记录磁盘及向量清理的待办
    __tablename__ = "file_cleanup_jobs"
    file_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    storage_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attempts: Mapped[int] = mapped_column(default=0)
    next_retry_at: Mapped[int] = mapped_column(default=0, index=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
