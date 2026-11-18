from typing import Literal
from pydantic import Field
from backend.app.tools.runtime import ToolArguments, register_tool
from backend.app.tools.account import account_available
from backend.app.tools.discovery import resource_entry
from backend.app.tools import workspace as impl


class Arguments(ToolArguments):
    query: str = Field(default='', max_length=200)
    knowledge_id: str | None = Field(default=None, max_length=36)
    count: int = Field(default=5, ge=1, le=50)
    skip: int = Field(default=0, ge=0)


@register_tool(name='search_knowledge_files', label='按名称查找知识文件', description='知识库：按文件名搜索本人知识库中的文件；query为空时列文件，knowledge_id可限定库。', arguments=Arguments, scope='always', uses_context=True, timeout=30, offer_when=resource_entry('search_knowledge_files'), available=account_available('knowledge_enabled'))
async def search_knowledge_files(context, **values):
    return await impl.search_knowledge_files(context, **values)
