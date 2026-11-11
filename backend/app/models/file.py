import time  # 生成时间戳
import uuid  # 生成文件唯一 ID
from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column  # 声明模型字段
from backend.app.core.database import Base  # 项目共用的模型基类

class File(Base):
    """保存上传文件的基本信息和文字提取结果。"""

    __tablename__ = "files"  # 对应数据库中的 files 表
    __table_args__ = (UniqueConstraint("user_id", "content_hash", name="uq_files_user_content_hash"),)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 同账号完整正文去重；旧重复记录保留ID且不强行合并
    chunk_key: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 当前SQL切片使用的策略签名
    indexed_content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True, nullable=True)  # 文件、正文、切片和向量统一继承文件所有者

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))  # 文件唯一 ID
    filename: Mapped[str] = mapped_column(String(255))  # 上传时的原始文件名，仅用于展示
    storage_path: Mapped[str] = mapped_column(String(255))  # 相对于 uploads 目录的保存路径
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)  # 请求声明的文件类型
    size: Mapped[int] = mapped_column(default=0)  # 实际读取的文件大小，单位字节
    text_content: Mapped[str] = mapped_column(Text, default="")  # 提取出的正文
    status: Mapped[str] = mapped_column(String(20), default="processing")  # processing / completed / failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)  # 处理失败的说明
    index_status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")  # pending / indexing / ready / failed
    index_error: Mapped[str | None] = mapped_column(Text, nullable=True)  # 向量索引失败说明
    index_key: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 建立索引时的向量配置标识
    index_task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)  # 预约任务，防止重复提交及旧任务覆盖新结果
    created_at: Mapped[int] = mapped_column(default=lambda: int(time.time()))  # 创建时间
    updated_at: Mapped[int] = mapped_column(default=lambda: int(time.time()), onupdate=lambda: int(time.time()))  # 更新时间

    @property
    def index_busy(self) -> bool:  # 排队与计算阶段都禁止删除和重新切片
        return self.index_task_id is not None or self.index_status == "indexing"

    @property
    def reused(self) -> bool:
        return bool(getattr(self, "_upload_reused", False))

    @property
    def index_current(self) -> bool:
        return not self.content_hash or self.content_hash == self.indexed_content_hash
