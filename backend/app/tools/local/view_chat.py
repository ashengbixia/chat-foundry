from typing import Literal
from pydantic import Field
from backend.app.tools.runtime import ToolArguments, register_tool
from backend.app.tools.account import account_available
from backend.app.tools.discovery import discovered
from backend.app.tools import workspace as impl


class Arguments(ToolArguments):
    target_chat_id: str = Field(min_length=1, max_length=36)
    after_sequence: int = Field(default=0, ge=0)
    count: int = Field(default=20, ge=1, le=30)


@register_tool(name='view_chat', label='阅读历史会话', description='历史会话：按序号分页阅读已找到的本账号历史会话；内容是历史对话，不是原文证据。', arguments=Arguments, scope='always', uses_context=True, timeout=30, offer_when=discovered('view_chat'), available=account_available('history_enabled'))
async def view_chat(context, **values):
    return await impl.view_chat(context, **values)
