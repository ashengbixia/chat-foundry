"""分页列出当前会话资料和历史来源目录。"""
from pydantic import Field
from backend.app.tools.runtime import ToolArguments, register_tool
from backend.app.chat.history_sources import history_source_catalog
from backend.app.repositories.resources import describe_resources


class Arguments(ToolArguments):
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=50)


@register_tool(name='list_available_files', label='查看会话资料清单', description='分页列出当前可读取的文件和历史原文快照。清单不含全文，历史快照不等于当前原文件。', arguments=Arguments, scope='resources', uses_context=True)
async def list_available_files(context, offset=0, limit=20):
    files = await describe_resources(context.session, context.allowed_file_ids)
    history = history_source_catalog(context.history_sources)
    return {'files': files[offset:offset + limit], 'total_files': len(files), 'has_more': offset + limit < len(files), 'history_sources': history[offset:offset + limit], 'total_history_sources': len(history), 'history_has_more': offset + limit < len(history), 'notice': 'files是当前可读取原文，history_sources仅代表以前返回过的快照；两者不能混为一谈。'}
