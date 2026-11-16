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
    knowledge_id: str | None = Field(default=None, max_length=36)


@register_tool(name='grep_knowledge_files', label='精确查找知识文件', description='知识库精确查找：在本人知识文件查找文字或正则，返回命中行和原文位置，不依赖向量索引。', arguments=Arguments, scope='always', uses_context=True, timeout=30, offer_when=discovered('grep_knowledge_files'), available=account_available('knowledge_enabled'))
async def grep_knowledge_files(context, **values):
    return await impl.grep_files(context, knowledge=True, **values)
