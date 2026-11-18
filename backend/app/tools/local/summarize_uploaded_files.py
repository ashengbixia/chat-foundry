"""按原文归纳当前授权资料。"""
import json
from pydantic import Field
from backend.app.tools.runtime import ToolArguments, register_tool
from backend.app.tools.summaries import summarize_uploaded_files as _summarize_uploaded_files, resolve_summary_cursor


class Arguments(ToolArguments):
    file_ids: list[str] | None = Field(default=None, max_length=200)
    focus: str = Field(default='', max_length=1000)
    resume_cursor: str | None = Field(default=None, min_length=36, max_length=36, description='上次结果continuation.cursor；省略file_ids和focus会沿用原值')


@register_tool(name='summarize_uploaded_files', label='分段读取并归纳资料', description='按原文分页归纳整份文件或整个知识库，返回覆盖范围与原文来源。省略file_ids处理全部当前文件；每次最多8段，未完成时用resume_cursor续接；相同参数重复调用复用结果。不完整时必须说明。', arguments=Arguments, scope='resource_files', timeout=600, uses_context=True, max_result_bytes=524288)
async def summarize_uploaded_files(context, **values):
    cursor = values.pop('resume_cursor', None)
    resume = await resolve_summary_cursor(context, cursor) if cursor else None
    if resume:
        if values.get('file_ids') is None:
            values['file_ids'] = resume['file_ids']
        if not values.get('focus'):
            values['focus'] = resume['focus']
    key = json.dumps([sorted(context.allowed_file_ids), values, cursor], sort_keys=True, ensure_ascii=False)
    cache = context.state.setdefault('summary_results', {})
    if key not in cache:
        cache[key] = await _summarize_uploaded_files(context.session, context.allowed_file_ids, context.connection,
            **values, check_access=context.check_access, resume_state=resume)
    return cache[key]
