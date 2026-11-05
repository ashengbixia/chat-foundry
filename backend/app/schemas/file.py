from typing import Literal  # 限制文件处理状态
from pydantic import BaseModel, ConfigDict  # 定义接口返回格式

class FileResponse(BaseModel):
    """返回文件基本信息，不返回磁盘路径和完整正文。"""
    model_config = ConfigDict(from_attributes=True)  # 从数据库模型对象读取字段
    id: str  # 文件唯一 ID
    filename: str  # 原始文件名
    content_type: str | None  # 请求声明的文件类型
    size: int  # 文件大小，单位字节
    status: Literal["processing", "completed", "failed"]  # 处理状态
    error: str | None  # 失败说明
    created_at: int  # 创建时间
    updated_at: int  # 更新时间

class FileContentResponse(BaseModel):
    """需要查看正文时单独查询，避免基本信息接口返回大量文字。"""
    id: str  # 文件 ID
    text_content: str  # 已提取的完整正文

class FileChunkResponse(BaseModel):
    """单个文本块。"""
    model_config = ConfigDict(from_attributes=True)
    id: str  # 块唯一ID
    file_id: str  # 所属文件
    chunk_index: int  # 块序号
    content: str  # 本块正文
    start_offset: int  # 原文起始位置
    end_offset: int  # 原文结束位置

class FileChunkPageResponse(BaseModel):
    """分页查看文本块。"""
    items: list[FileChunkResponse]  # 本页内容
    total: int  # 总块数
    offset: int  # 本次跳过的块数
    limit: int  # 每页数量

class FileChunkBuildResponse(BaseModel):
    """已有文件重新切片的结果。"""
    file_id: str  # 文件ID
    chunk_count: int  # 本次生成的块数
