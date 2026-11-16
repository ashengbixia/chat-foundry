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


@register_tool(name='query_knowledge_bases', label='语义发现知识库', description='知识库：按语义匹配本页知识库名称和描述，帮助选择库；结果不是正文依据。', arguments=Arguments, scope='always', uses_context=True, timeout=120, offer_when=discovered('query_knowledge_bases'), available=account_available('knowledge_enabled'))
async def query_knowledge_bases(context, **values):
    return await impl.query_knowledge_bases(context, **values)
