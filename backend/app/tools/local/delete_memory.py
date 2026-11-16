from typing import Literal
from pydantic import Field
from backend.app.tools.runtime import ToolArguments, register_tool
from backend.app.tools.account import account_available
from backend.app.tools.discovery import discovered
from backend.app.tools import memory_ops as impl


class Arguments(ToolArguments):
    memory_id: str = Field(min_length=1, max_length=36)
    revision: int = Field(ge=1, description='先读取记忆取得当前版本，防止覆盖其他修改')


@register_tool(name='delete_memory', label='删除长期记忆', description='长期记忆：根据用户删除或忘记该信息的要求删除指定记忆，必须提供当前版本。', arguments=Arguments, scope='always', uses_context=True, timeout=30, offer_when=discovered('delete_memory'), available=account_available('memory_enabled'))
async def delete_memory(context, **values):
    return await impl.remove(context, **values)
