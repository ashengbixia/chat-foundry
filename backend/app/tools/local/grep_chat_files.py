from typing import Literal
from pydantic import Field
from backend.app.tools.runtime import ToolArguments, register_tool
from backend.app.tools.account import account_available
from backend.app.tools.discovery import discovered
from backend.app.tools import workspace as impl


class Arguments(ToolArguments):
    pattern: str = Field(min_length=1, max_length=500)
    file_id: str | None = Field(default=None, max_length=36)
    case_insensitive: bool = False
    use_regex: bool = False
    count_only: bool = False
    count: int = Field(default=20, ge=1, le=30)
    offset: int = Field(default=0, ge=0)


@register_tool(name='grep_chat_files', label='精确查找会话文件', description='精确查找：在授权会话文件正文查找文字或显式开启正则，返回命中行与字符位置，不依赖向量索引；可只统计数量。', arguments=Arguments, scope='files', uses_context=True, timeout=30, offer_when=discovered('grep_chat_files'))
async def grep_chat_files(context, **values):
    return await impl.grep_files(context, **values)
