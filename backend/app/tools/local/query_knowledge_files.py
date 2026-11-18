from typing import Literal
from pydantic import Field
from backend.app.tools.runtime import ToolArguments, register_tool
from backend.app.tools.account import account_available
from backend.app.tools.discovery import resource_entry
from backend.app.tools import workspace as impl


class Arguments(ToolArguments):
    query: str = Field(min_length=1, max_length=200)
    knowledge_id: str | None = Field(default=None, max_length=36)
    count: int = Field(default=5, ge=1, le=10)
    knowledge_ids: list[str] | None = Field(default=None, min_length=1, max_length=50, description='限定多个本人知识库，与knowledge_id互斥')


@register_tool(name='query_knowledge_files', label='检索知识库正文', description='知识库：对本人可访问库进行向量检索，knowledge_id可限定库。返回候选原文片段，不是全文；与名称搜索和精确查找不同。', arguments=Arguments, scope='always', uses_context=True, timeout=120, offer_when=resource_entry('query_knowledge_files'), available=account_available('knowledge_enabled'))
async def query_knowledge_files(context, **values):
    return await impl.query_knowledge_files(context, **values)
