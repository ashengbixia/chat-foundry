"""加载受信任的工具模块，并向聊天流程提供统一入口。"""
from backend.app.tools.runtime import ToolContext, registry
from backend.app.tools.local import (  # 显式加载受信任模块；文件名、处理函数名与工具名一致。
    get_current_time,
    read_uploaded_file,
    search_uploaded_files,
    list_available_files,
    read_history_sources,
    summarize_uploaded_files,
    read_webpage,
    search_web,
    delegate_task,
    get_subagent_task,
    cancel_subagent_task,
)


def get_available_tools(allowed_file_ids: set[str], has_history=False, resource_tools=False, *, context=None):
    if context is None:
        context = ToolContext(allowed_file_ids=set(allowed_file_ids), history_sources={'available': {}} if has_history else {}, resource_tools=resource_tools)
    else:
        context.allowed_file_ids &= set(allowed_file_ids)
    return registry.definitions(context)


def get_tool_label(name):
    return registry.label(name)


async def execute_tool(name, arguments, session=None, allowed_file_ids=None, *, context: ToolContext | None = None):
    if context is None:  # 兼容已有独立调用；聊天流程传入一次回答共用的上下文
        context = ToolContext(session=session, allowed_file_ids=set(allowed_file_ids or set()))
    return await registry.execute(name, arguments, context)


TOOL_DEFINITIONS = get_available_tools({'schema-only'})  # 兼容旧客户端导入，聊天仍动态生成可用清单
