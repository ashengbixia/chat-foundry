"""检索当前授权附件与知识库。"""
from pydantic import Field
from backend.app.tools.runtime import ToolArguments, register_tool
from backend.app.tools.files import search_uploaded_files as _search_uploaded_files


class Arguments(ToolArguments):
    query: str = Field(min_length=1, max_length=1000)
    file_id: str | None = None
    file_ids: list[str] | None = Field(default=None, min_length=1, max_length=200)
    limit: int = Field(default=5, ge=1, le=10)


@register_tool(name='search_uploaded_files', label='检索附件内容', description='检索当前会话附件和所选知识库的相关片段。可用file_id或file_ids缩小范围，两者互斥。结果不是完整文件，distance不是正确率；需要全文或上下文时使用原文读取工具。', arguments=Arguments, scope='files', timeout=120, uses_context=True)
async def search_uploaded_files(context, **values):
    return await _search_uploaded_files(session=context.session, allowed_file_ids=context.allowed_file_ids, **values)
