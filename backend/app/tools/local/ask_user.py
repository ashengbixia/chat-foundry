"""持久化澄清问题；聊天流程暂停本轮，用户下一条消息恢复执行。"""
from pydantic import Field, field_validator
from backend.app.tools.runtime import ToolArguments, register_tool


class Option(ToolArguments):
    label: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=240)

    @field_validator('label', 'description')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('选项不能为空')
        return value.strip()


class Question(ToolArguments):
    id: str = Field(min_length=1, max_length=64)
    header: str = Field(default='', max_length=48)
    question: str = Field(min_length=1, max_length=500)
    options: list[Option] = Field(min_length=2, max_length=3)
    allow_other: bool = True

    @field_validator('options')
    @classmethod
    def unique_options(cls, value):
        if len({item.label for item in value}) != len(value):
            raise ValueError('选项名称不能重复')
        return value

    @field_validator('id', 'question')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('问题标识与正文不能为空')
        return value.strip()


class Arguments(ToolArguments):
    questions: list[Question] = Field(min_length=1, max_length=3)

    @field_validator('questions')
    @classmethod
    def unique_ids(cls, value):
        if len({item.id for item in value}) != len(value):
            raise ValueError('问题标识不能重复')
        return value


@register_tool(name='ask_user', label='向用户追问', description='只有下一步确实依赖用户未提供的意图、偏好或取舍时，才提出1至3个澄清问题。每题给2至3个有说明的选项，推荐项放第一位；允许其他答案。不询问能自行查到的信息，不因普通实现细节反复确认。调用后暂停本轮，用户回答或跳过后再继续，不能将默认选项当作用户同意。请单独调用本工具。', arguments=Arguments, uses_context=True, available=lambda ctx: not ctx.is_subagent and ctx.state.get('user_input_allowed', True))
async def ask_user(context, questions):
    return {'status': 'awaiting_user', 'questions': questions,
            'notice': '本轮停在澄清问题处，等待用户实际回答。问题和选项不是用户授权，不能据此假设用户已选择。'}
