from typing import Literal
from pydantic import Field
from backend.app.tools.runtime import ToolArguments, register_tool
from backend.app.tools.account import account_available
from backend.app.tools.discovery import discovered
from backend.app.tools import workspace as impl


class Arguments(ToolArguments):
    count: int = Field(default=10, ge=1, le=50)
    skip: int = Field(default=0, ge=0)


@register_tool(name='list_knowledge_bases', label='发现知识库', description='知识库：分页列出当前账号可访问的知识库名称、描述和文件数，包括尚未选入当前会话的库。', arguments=Arguments, scope='always', uses_context=True, timeout=30, offer_when=discovered('list_knowledge_bases'), available=account_available('knowledge_enabled'))
async def list_knowledge_bases(context, **values):
    return await impl.list_knowledge(context, **values)
