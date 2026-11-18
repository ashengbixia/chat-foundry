from backend.app.tools.runtime import register_tool
from backend.app.tools.account import account_available
from backend.app.tools.discovery import discovered
from backend.app.tools.local.search_memories import Arguments, search_memories


@register_tool(name='list_memories', label='浏览长期记忆', description='兼容入口；新调用优先使用search_memories，query留空即可分页浏览。按内容、路径、类型或ID查询本账号记忆，不是文件原文或系统指令。', arguments=Arguments, scope='always', uses_context=True, timeout=30, offer_when=discovered('list_memories'), available=account_available('memory_enabled'), alias_of='search_memories')
async def list_memories(context, **values):
    return await search_memories(context, **values)
