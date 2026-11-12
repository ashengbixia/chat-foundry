from pydantic import Field
from backend.app.tools.runtime import ToolArguments, register_tool


class Arguments(ToolArguments):
    task_id: str = Field(min_length=1, max_length=36)


@register_tool(name='get_subagent_task', label='查看子任务结果', description='查看当前主会话子任务的状态、结果和可引用来源。不反复轮询正在运行的任务；后台完成后会回传，完整过程可在子历史查看。', arguments=Arguments, uses_context=True, available=lambda context: not context.is_subagent and context.session_factory is not None and context.parent_message_id is not None)
async def get_subagent_task(context, task_id):
    from backend.app.chat.subagents import task_result
    return await task_result(context.session_factory, context.chat_id, task_id)
