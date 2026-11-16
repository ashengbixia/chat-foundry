"""按需读取已返回的历史原文快照。"""
from pydantic import Field
from backend.app.tools.runtime import ToolArguments, register_tool
from backend.app.chat.history_sources import read_history_sources as _read_history_sources


class Arguments(ToolArguments):
    source_ids: list[str] = Field(default_factory=list, max_length=4, description='从历史目录选择编号；留空则分页查询更早快照目录')
    before_sequence: int | None = Field(default=None, ge=1)
    query: str = Field(default='', max_length=200, description='查询目录时筛选本页文件名或正文')
    limit: int = Field(default=4, ge=1, le=16, description='目录每页扫描的助手消息数；结果过大时调低')
    offset: int = Field(default=0, ge=0, description='快照内部的字符偏移，续读时使用pages中的next_offset')
    max_chars: int = Field(default=5000, ge=1, le=12000)


@register_tool(name='read_history_sources', label='查阅历史原文快照', description='读取历史工具返回的原文快照，并取得本轮可引用编号。删除库或文件后仍可讨论已返回的片段，不能获取快照以外的原文。', arguments=Arguments, scope='history', uses_context=True)
async def read_history_sources(context, source_ids=None, offset=0, max_chars=5000, before_sequence=None, query='', limit=4):
    source_ids = source_ids or []
    if not source_ids:
        if context.history_lookup:
            _, catalog = await context.history_lookup(before_sequence=before_sequence, query=query, limit=limit)
            return catalog
        from backend.app.chat.history_sources import history_source_catalog
        return {'sources': history_source_catalog(context.history_sources)}
    sources = dict(context.history_sources)
    missing = [key for key in source_ids if key not in sources]
    if missing and context.history_lookup:
        older, _ = await context.history_lookup(source_ids=missing)
        sources.update(older)
    return _read_history_sources(source_ids, sources, offset, max_chars)
