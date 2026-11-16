from typing import Literal
from pydantic import Field
from backend.app.tools.runtime import ToolArguments, register_tool
from backend.app.tools.account import account_available
from backend.app.tools.discovery import discovered
from backend.app.tools import workspace as impl


class Arguments(ToolArguments):
    command: str = Field(min_length=1, max_length=1000)
    knowledge_id: str | None = Field(default=None, max_length=36)


@register_tool(name='kb_exec', label='浏览知识库虚拟文件', description='知识库文件浏览：只读命令ls、tree、find 关键词、grep 文字 [file_id]、cat/head/tail/wc/stat file_id、sed 起始行,结束行p file_id。平面目录，不是真实Shell，不执行系统命令，不访问服务器磁盘。', arguments=Arguments, scope='always', uses_context=True, timeout=30, offer_when=discovered('kb_exec'), available=account_available('knowledge_enabled'))
async def kb_exec(context, **values):
    return await impl.kb_exec(context, **values)
