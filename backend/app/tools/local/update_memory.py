from typing import Literal
from pydantic import Field
from backend.app.tools.runtime import ToolArguments, register_tool
from backend.app.tools.account import account_available
from backend.app.tools.discovery import discovered
from backend.app.tools import memory_ops as impl


class Arguments(ToolArguments):
    memory_id: str = Field(min_length=1, max_length=36)
    revision: int = Field(ge=1, description='先读取记忆取得当前版本，防止覆盖其他修改')
    content: str = Field(min_length=1, max_length=8000)
    type: Literal['user','context'] | None = None
    path: str | None = Field(default=None, max_length=300)


@register_tool(name='update_memory', label='更新长期记忆', description='长期记忆：更新已有记忆正文或分组，必须使用最新版本；不要为迎合新问题擅自改写用户偏好。', arguments=Arguments, scope='always', uses_context=True, timeout=30, offer_when=discovered('update_memory'), available=account_available('memory_enabled'))
async def update_memory(context, **values):
    return await impl.update(context, **values)
