"""主模型进入子智能体的统一工具入口。"""
from pydantic import Field
from backend.app.core.config import settings
from backend.app.tools.runtime import ToolArguments, register_tool


class Arguments(ToolArguments):
    task: str = Field(min_length=1, max_length=3000, description='明确、独立的子任务及预期结果')
    task_context: str = Field(default='', max_length=6000, description='必要背景，不要复制全部聊天记录')
    background: bool = Field(default=False, description='false等待结果；true后台运行，完成后回传主会话')
    file_ids: list[str] = Field(default_factory=list, max_length=20, description='明确分配的当前文件，省略表示不分配文件')
    history_source_ids: list[str] = Field(default_factory=list, max_length=4)
    tool_names: list[str] | None = Field(default=None, max_length=20, description='可缩小子任务工具范围，不能授予父任务没有的工具')


def available(context):
    return settings.subagents_enabled and not context.is_subagent and context.session_factory is not None and context.parent_message_id is not None


@register_tool(name='delegate_task', label='委派子任务', description='把独立研究、资料阅读或核对任务交给子智能体。子智能体使用独立会话与同一模型，可使用分配的工具；默认等待结果，background=true时后台运行并回传。只传必要上下文和明确授权资料；简单问题直接回答。子任务结果仍需核实，引用只能采用主回答重新分配的编号。', arguments=Arguments, uses_context=True, timeout=1920, available=available)
async def delegate_task(context, **values):
    from backend.app.chat.subagents import delegate
    return await delegate(context, **values)
