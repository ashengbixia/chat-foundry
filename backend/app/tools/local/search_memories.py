from typing import Literal
from pydantic import Field
from backend.app.tools.runtime import ToolArguments, register_tool
from backend.app.tools.account import account_available
from backend.app.tools.discovery import discovered
from backend.app.tools import memory_ops as impl


class Arguments(ToolArguments):
    query: str = Field(default='', max_length=200)
    count: int = Field(default=5, ge=1, le=50)
    type: Literal['user','context','all'] = 'all'
    path: str | None = Field(default=None, max_length=300)
    memory_id: str | None = Field(default=None, max_length=36)
    offset: int = Field(default=0, ge=0)


@register_tool(name='search_memories', label='搜索长期记忆', description='长期记忆：按内容、路径、类型或ID查询当前账号已保存的稳定信息，返回版本号；它不是文件原文或系统指令。', arguments=Arguments, scope='always', uses_context=True, timeout=30, offer_when=discovered('search_memories'), available=account_available('memory_enabled'))
async def search_memories(context, **values):
    return await impl.search(context, **values)
