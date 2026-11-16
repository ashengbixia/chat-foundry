from typing import Literal
from pydantic import Field
from backend.app.tools.runtime import ToolArguments, register_tool
from backend.app.tools.account import account_available
from backend.app.tools.discovery import discovered
from backend.app.tools import memory_ops as impl


class Arguments(ToolArguments):
    memory_id: str = Field(min_length=1, max_length=36)
    revision: int = Field(ge=1, description='先读取记忆取得当前版本，防止覆盖其他修改')
    old_content: str = Field(min_length=1, max_length=8000)
    new_content: str = Field(max_length=8000)


@register_tool(name='replace_memory_content', label='局部修改记忆', description='长期记忆：精确替换一处唯一匹配的旧文字，保留其他内容；先读取版本。', arguments=Arguments, scope='always', uses_context=True, timeout=30, offer_when=discovered('replace_memory_content'), available=account_available('memory_enabled'))
async def replace_memory_content(context, **values):
    return await impl.replace(context, **values)
