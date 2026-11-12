from pydantic import BaseModel, ConfigDict, Field


class ChatCreate(BaseModel):
    """创建会话时接收的数据。"""

    model_config = ConfigDict(str_strip_whitespace=True)#去空格

    title: str = Field(default="新会话",min_length=1,max_length=200,)
    knowledge_base_ids: list[str] = Field(default_factory=list, max_length=5)
    web_search_enabled: bool = Field(default=False, strict=True)


class ChatFeatures(BaseModel):
    model_config = ConfigDict(extra='forbid')
    web_search_enabled: bool = Field(strict=True)


class KnowledgeReference(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str


class ChatFileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    file_id: str
    filename: str
    size: int
    deleted: bool


class ChatResponse(BaseModel):
    """返回给客户端的会话信息。"""

    # 允许从对象属性读取数据，比如从数据库对象读取
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    created_at: int
    updated_at: int
    knowledge_bases: list[KnowledgeReference] = Field(default_factory=list)
    files: list[ChatFileResponse] = Field(default_factory=list)
    web_search_enabled: bool = False
    title_status: str = 'manual'

class MessageAttachmentResponse(BaseModel):  # 消息附件只返回展示所需的基本信息
    model_config = ConfigDict(from_attributes=True)
    file_id: str  # 文件 ID
    filename: str  # 发送时的文件名
    size: int  # 文件大小
    deleted: bool = False  # 原文件是否已删除，历史名称和大小仍保留

class MessageResponse(BaseModel):
    """返回给客户端的消息信息。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    chat_id: str
    sequence: int
    role: str
    content: str
    output: list
    attachments: list[MessageAttachmentResponse] = Field(default_factory=list)  # 旧消息没有附件时返回空列表
    status: str
    model: str | None
    usage: dict | None
    error: str | None
    created_at: int
    updated_at: int

class MessagePageResponse(BaseModel):  # 一批消息和继续加载所需的信息
    items: list[MessageResponse]  # 本批消息，按序号从小到大排列
    has_more: bool  # 是否还有更早消息
    next_before_sequence: int | None  # 下一批查询边界，没有下一批时为 None

class MessageCreate(BaseModel):
    """客户端提交的用户消息。"""
    model_config = ConfigDict(str_strip_whitespace=True)
    content: str = Field(min_length=1,max_length=10000,)
    file_ids: list[str] = Field(default_factory=list, max_length=3)  # 每条用户消息最多3份附件

class ChatRename(BaseModel):  # 修改会话标题的请求格式
    model_config = ConfigDict(str_strip_whitespace=True)  # 自动去掉两端空白
    title: str = Field(min_length=1, max_length=200)  # 必须明确提供新标题
