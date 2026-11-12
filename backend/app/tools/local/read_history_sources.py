"""按需读取已返回的历史原文快照。"""
from pydantic import Field
from backend.app.tools.runtime import ToolArguments, register_tool
from backend.app.chat.history_sources import read_history_sources as _read_history_sources


class Arguments(ToolArguments):
    source_ids: list[str] = Field(min_length=1, max_length=4)
    offset: int = Field(default=0, ge=0, description='快照内部的字符偏移，续读时使用pages中的next_offset')
    max_chars: int = Field(default=5000, ge=1, le=12000)


@register_tool(name='read_history_sources', label='查阅历史原文快照', description='读取历史工具返回的原文快照，并取得本轮可引用编号。删除库或文件后仍可讨论已返回的片段，不能获取快照以外的原文。', arguments=Arguments, scope='history', uses_context=True)
async def read_history_sources(context, source_ids, offset=0, max_chars=5000):
    return _read_history_sources(source_ids, context.history_sources, offset, max_chars)
