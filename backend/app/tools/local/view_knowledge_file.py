from typing import Literal
from pydantic import Field
from backend.app.tools.runtime import ToolArguments, register_tool
from backend.app.tools.account import account_available
from backend.app.tools.discovery import resource_entry
from backend.app.tools import workspace as impl


class Arguments(ToolArguments):
    file_id: str = Field(min_length=1, max_length=36)
    offset: int = Field(default=0, ge=0)
    max_chars: int = Field(default=6000, ge=1, le=12000)
    line_numbers: bool = False
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)


@register_tool(name='view_knowledge_file', label='按行浏览知识文件', description='知识库：按字符或行范围读取本人知识库文件，可显示行号；原文可引用，不因已知文件ID扩大权限。', arguments=Arguments, scope='always', uses_context=True, timeout=30, offer_when=resource_entry('view_knowledge_file'), available=account_available('knowledge_enabled'))
async def view_knowledge_file(context, **values):
    return await impl.view_file(context, knowledge=True, **values)
