from typing import Literal
from pydantic import Field
from backend.app.tools.runtime import ToolArguments, register_tool
from backend.app.tools.account import account_available
from backend.app.tools.discovery import discovered
from backend.app.tools import memory_ops as impl


class Arguments(ToolArguments):
    query: str = Field(default='', max_length=200)
    count: int = Field(default=100, ge=1, le=100)
    type: Literal['user','context','all'] = 'all'
    offset: int = Field(default=0, ge=0)


@register_tool(name='list_memory_paths', label='浏览记忆路径', description='长期记忆：列出当前账号记忆分组路径及数量，便于整理和避免重复保存。', arguments=Arguments, scope='always', uses_context=True, timeout=30, offer_when=discovered('list_memory_paths'), available=account_available('memory_enabled'))
async def list_memory_paths(context, **values):
    return await impl.paths(context, **values)
