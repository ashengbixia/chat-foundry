from pydantic import Field
from backend.app.tools.runtime import ToolArguments, register_tool


class Arguments(ToolArguments):
    task_id: str = Field(min_length=1, max_length=36)


@register_tool(name='cancel_subagent_task', label='停止子任务', description='用户要求停止当前会话的某个子任务时调用。已经结束的任务不重新执行。', arguments=Arguments, uses_context=True, available=lambda context: not context.is_subagent and context.session_factory is not None and context.parent_message_id is not None)
async def cancel_subagent_task(context, task_id):
    from backend.app.chat.subagents import cancel_task
    return await cancel_task(context.session_factory, context.chat_id, task_id)
