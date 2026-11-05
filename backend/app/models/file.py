import time  # 生成时间戳
import uuid  # 生成文件唯一 ID
from sqlalchemy import String, Text  # 数据库字段类型
from sqlalchemy.orm import Mapped, mapped_column  # 声明模型字段
from backend.app.core.database import Base  # 项目共用的模型基类

class File(Base):
    """保存上传文件的基本信息和文字提取结果。"""

    __tablename__ = "files"  # 对应数据库中的 files 表

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))  # 文件唯一 ID
    filename: Mapped[str] = mapped_column(String(255))  # 上传时的原始文件名，仅用于展示
    storage_path: Mapped[str] = mapped_column(String(255))  # 相对于 uploads 目录的保存路径
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)  # 请求声明的文件类型
    size: Mapped[int] = mapped_column(default=0)  # 实际读取的文件大小，单位字节
    text_content: Mapped[str] = mapped_column(Text, default="")  # 提取出的正文
    status: Mapped[str] = mapped_column(String(20), default="processing")  # processing / completed / failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)  # 处理失败的说明
    created_at: Mapped[int] = mapped_column(default=lambda: int(time.time()))  # 创建时间
    updated_at: Mapped[int] = mapped_column(default=lambda: int(time.time()), onupdate=lambda: int(time.time()))  # 更新时间
