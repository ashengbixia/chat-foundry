from pydantic import Field, field_validator
from backend.app.tools.runtime import ToolArguments, register_tool
from backend.app.tools.account import account_available
from backend.app.tools.discovery import resource_entry
from backend.app.tools.workspace import search_files as find_files


class Arguments(ToolArguments):
    query: str = Field(default='', max_length=200, description='文件名中的文字；为空时分页浏览名称，不搜索正文')
    count: int = Field(default=10, ge=1, le=30)
    skip: int = Field(default=0, ge=0)

    @field_validator('query')
    @classmethod
    def nonblank(cls, value):
        return value.strip()


@register_tool(name='search_files', label='查找本人独立文件', description='按文件名查找本人不属于知识库的独立上传文件；query为空时分页浏览名称。只返回目录元数据，不读取正文；正文用view_account_file，知识库成员使用知识库入口，不要把文件名当内容依据。', arguments=Arguments, uses_context=True, available=account_available('files_enabled'), offer_when=resource_entry('search_files', 'files'))
async def search_files(context, **values):
    return await find_files(context, **values)
