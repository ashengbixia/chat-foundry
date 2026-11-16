from typing import Literal
from pydantic import Field
from backend.app.tools.runtime import ToolArguments, register_tool
from backend.app.tools.account import account_available
from backend.app.tools.discovery import discovered
from backend.app.tools import memory_ops as impl


class Arguments(ToolArguments):
    path: str = Field(min_length=1, max_length=300)
    count: int = Field(default=20, ge=1, le=50)
    offset: int = Field(default=0, ge=0)


@register_tool(name='read_memory_path', label='读取记忆分组', description='长期记忆：分页读取指定路径及子路径的稳定信息。', arguments=Arguments, scope='always', uses_context=True, timeout=30, offer_when=discovered('read_memory_path'), available=account_available('memory_enabled'))
async def read_memory_path(context, **values):
    return await impl.search(context, **values)
