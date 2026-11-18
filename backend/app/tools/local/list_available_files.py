"""分页列出当前会话资料和历史来源目录。"""
from pydantic import Field
from backend.app.tools.runtime import ToolArguments, register_tool
from backend.app.chat.history_sources import history_source_catalog
from backend.app.repositories.resources import describe_resources


class Arguments(ToolArguments):
    query: str = Field(default='', max_length=200, description='可选，按文件名筛选当前资料和历史来源目录')
    offset: int = Field(default=0, ge=0, description='当前文件清单分页位置')
    history_offset: int | None = Field(default=None, ge=0, description='历史快照清单独立分页位置；省略沿用offset')
    limit: int = Field(default=20, ge=1, le=50)


@register_tool(name='list_available_files', label='查看会话资料清单', description='分页列出当前可读取的文件和历史原文快照。清单不含全文，历史快照不等于当前原文件。', arguments=Arguments, scope='resources', uses_context=True)
async def list_available_files(context, offset=0, limit=20, query='', history_offset=None):
    history_offset = offset if history_offset is None else history_offset
    files = await describe_resources(context.session, context.allowed_file_ids)
    history = history_source_catalog(context.history_sources)
    if query:
        files = [item for item in files if query.casefold() in item['filename'].casefold()]
        history = [item for item in history if query.casefold() in item['filename'].casefold()]
    return {'files': files[offset:offset + limit], 'total_files': len(files), 'has_more': offset + limit < len(files), 'next_offset': offset + limit if offset + limit < len(files) else None, 'history_sources': history[history_offset:history_offset + limit], 'total_history_sources': len(history), 'history_has_more': history_offset + limit < len(history), 'next_history_offset': history_offset + limit if history_offset + limit < len(history) else None, 'notice': 'files是当前可读取原文，history_sources仅代表以前返回过的快照；两者不能混为一谈。'}
