"""读取当前授权附件的正文。"""
from pydantic import Field
from backend.app.tools.runtime import ToolArguments, register_tool
from backend.app.tools.files import read_uploaded_file as _read_uploaded_file


class Arguments(ToolArguments):
    file_id: str = Field(min_length=1)
    offset: int = Field(default=0, ge=0, le=2147483647)
    max_chars: int = Field(default=6000, ge=1, le=12000)


@register_tool(name='read_uploaded_file', label='读取附件正文', description='读取当前会话允许文件的正文，按字符分页。has_more为true时通过next_offset继续读取，不能把片段当作全文。', arguments=Arguments, scope='files', uses_context=True)
async def read_uploaded_file(context, **values):
    return await _read_uploaded_file(session=context.session, allowed_file_ids=context.allowed_file_ids, **values)
