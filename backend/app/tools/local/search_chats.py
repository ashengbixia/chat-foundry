from typing import Literal
from pydantic import Field
from backend.app.tools.runtime import ToolArguments, register_tool
from backend.app.tools.account import account_available
from backend.app.tools.discovery import discovered
from backend.app.tools import workspace as impl


class Arguments(ToolArguments):
    query: str = Field(min_length=1, max_length=200)
    count: int = Field(default=5, ge=1, le=20)
    offset: int = Field(default=0, ge=0)
    start_timestamp: int | None = Field(default=None, ge=0)
    end_timestamp: int | None = Field(default=None, ge=0)


@register_tool(name='search_chats', label='搜索历史会话', description='历史会话：搜索当前账号以前的会话标题和消息正文，排除当前会话，支持时间范围；不搜索其他账号。', arguments=Arguments, scope='always', uses_context=True, timeout=30, offer_when=discovered('search_chats'), available=account_available('history_enabled'))
async def search_chats(context, **values):
    return await impl.search_chats(context, **values)
