from typing import Literal
from pydantic import Field
from backend.app.tools.runtime import ToolArguments, register_tool
from backend.app.tools.account import account_available
from backend.app.tools.discovery import discovered
from backend.app.tools import workspace as impl


class Arguments(ToolArguments):
    query: str = Field(min_length=1, max_length=200)
    count: int = Field(default=5, ge=1, le=50)
    skip: int = Field(default=0, ge=0)


@register_tool(name='search_knowledge_bases', label='查找知识库名称', description='知识库：按名称或描述的文字查找当前账号知识库，不搜索文件正文。', arguments=Arguments, scope='always', uses_context=True, timeout=30, offer_when=discovered('search_knowledge_bases'), available=account_available('knowledge_enabled'))
async def search_knowledge_bases(context, **values):
    return await impl.list_knowledge(context, **values)
