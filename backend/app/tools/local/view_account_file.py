from pydantic import Field
from backend.app.tools.runtime import ToolArguments, register_tool
from backend.app.tools.account import account_available
from backend.app.tools.discovery import resource_entry
from backend.app.tools.workspace import view_file


class Arguments(ToolArguments):
    file_id: str = Field(min_length=1, max_length=36)
    offset: int = Field(default=0, ge=0)
    max_chars: int = Field(default=6000, ge=1, le=12000)
    line_numbers: bool = False
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)


@register_tool(name='view_account_file', label='读取本人独立文件', description='按需读取本人不属于知识库的独立上传文件，可按字符或行范围分页。先用search_files确认文件身份；知识库成员使用知识库入口。不会自动加入会话或扩大子任务范围，只有真实返回的正文可引用。', arguments=Arguments, uses_context=True, available=account_available('files_enabled'), offer_when=resource_entry('view_account_file', 'files'))
async def view_account_file(context, **values):
    return await view_file(context, account=True, **values)
