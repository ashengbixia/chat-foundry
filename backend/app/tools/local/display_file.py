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
    knowledge: bool = False
    account: bool = Field(default=False, description='展示本人独立文件，需要文件发现权限；与knowledge互斥')


@register_tool(name='display_file', label='展示文件正文', description='文件浏览：在本条回答中向用户展示文件正文预览；可展示当前会话文件，knowledge=true时展示本人知识库文件，account=true时展示独立文件；两个参数互斥。只展示提取正文，不是原PDF/Office排版。成功后不必重复粘贴相同正文。', arguments=Arguments, scope='always', uses_context=True, timeout=30, offer_when=discovered('display_file'))
async def display_file(context, **values):
    return await impl.display_file(context, **values)
