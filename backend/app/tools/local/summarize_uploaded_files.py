"""按原文归纳当前授权资料。"""
from pydantic import Field
from backend.app.tools.runtime import ToolArguments, register_tool
from backend.app.tools.summaries import summarize_uploaded_files as _summarize_uploaded_files


class Arguments(ToolArguments):
    file_ids: list[str] | None = Field(default=None, max_length=200)
    focus: str = Field(default='', max_length=1000)


@register_tool(name='summarize_uploaded_files', label='分段读取并归纳资料', description='按原文分页归纳整份文件或整个知识库，返回覆盖范围与原文来源。省略file_ids处理全部当前文件；每轮最多执行一次，重复调用复用结果。不完整时必须说明。', arguments=Arguments, scope='resource_files', timeout=600, uses_context=True, once_per_turn=True, max_result_bytes=524288)
async def summarize_uploaded_files(context, **values):
    return await _summarize_uploaded_files(context.session, context.allowed_file_ids, context.connection, **values, check_access=context.check_access)
