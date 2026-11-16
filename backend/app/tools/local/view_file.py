from typing import Literal
from pydantic import Field
from backend.app.tools.runtime import ToolArguments, register_tool
from backend.app.tools.account import account_available
from backend.app.tools.discovery import discovered
from backend.app.tools import workspace as impl


class Arguments(ToolArguments):
    file_id: str = Field(min_length=1, max_length=36)
    offset: int = Field(default=0, ge=0)
    max_chars: int = Field(default=6000, ge=1, le=12000)
    line_numbers: bool = False
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)


@register_tool(name='view_file', label='按行浏览会话文件', description='文件浏览：按字符或行范围读取当前授权文件，可显示行号，原文仍可引用；不读取服务器磁盘。', arguments=Arguments, scope='files', uses_context=True, timeout=30, offer_when=discovered('view_file'))
async def view_file(context, **values):
    return await impl.view_file(context, **values)
