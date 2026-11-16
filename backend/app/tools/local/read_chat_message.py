from typing import Literal
from pydantic import Field
from backend.app.tools.runtime import ToolArguments, register_tool
from backend.app.tools.account import account_available
from backend.app.tools.discovery import discovered
from backend.app.tools import workspace as impl


class Arguments(ToolArguments):
    target_chat_id: str = Field(min_length=1, max_length=36)
    sequence: int = Field(ge=1)
    offset: int = Field(default=0, ge=0)
    max_chars: int = Field(default=6000, ge=1, le=12000)


@register_tool(name='read_chat_message', label='续读历史消息', description='历史会话：按字符分页续读单条长消息，不开放该会话原附件的读取权限。', arguments=Arguments, scope='always', uses_context=True, timeout=30, offer_when=discovered('read_chat_message'), available=account_available('history_enabled'))
async def read_chat_message(context, **values):
    return await impl.read_chat_message(context, **values)
