"""工具注册与执行基础层，不依赖聊天流程或具体工具。"""
import inspect
import json
import re
from dataclasses import dataclass, field
from collections.abc import Callable
import anyio
from pydantic import BaseModel, ConfigDict, ValidationError


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)  # 模型不能注入数据库连接、权限范围或额外参数


@dataclass
class ToolContext:
    session: object = None
    allowed_file_ids: set[str] = field(default_factory=set)
    history_sources: dict = field(default_factory=dict)
    connection: object = None
    check_access: Callable | None = None
    resource_tools: bool = False
    chat_id: str | None = None
    cache: dict = field(default_factory=dict)  # 一次回答独有，不能跨用户或跨回答共享
    state: dict = field(default_factory=dict)  # 工具私有的单轮状态，按工具名称分区
    history: list | None = None  # 真实会话记录，用于需要核对用户输入的工具；不是模型传入的参数
    web_search_enabled: bool = False  # 当前回答开始时的会话功能快照
    is_subagent: bool = False
    parent_message_id: str | None = None
    call_id: str | None = None
    session_factory: object = None
    allowed_tools: frozenset[str] | None = None
    approved_urls: frozenset[str] | None = None

    async def refresh_access(self):
        if self.check_access is not None:
            self.allowed_file_ids &= set(await self.check_access())


@dataclass(frozen=True)
class ToolSpec:
    name: str
    label: str
    description: str
    arguments: type[ToolArguments]
    handler: Callable
    scope: str = 'always'
    timeout: float = 30
    uses_context: bool = False
    once_per_turn: bool = False
    max_result_bytes: int = 262144
    available: Callable | None = None
    offer_when: Callable | None = None  # 动态额度只影响模型清单；执行入口仍返回具体限额原因

    def is_available(self, context: ToolContext) -> bool:
        if context.allowed_tools is not None and self.name not in context.allowed_tools:
            return False
        if self.scope == 'files' and not context.allowed_file_ids:
            return False
        if self.scope == 'history' and not (context.resource_tools and context.history_sources):
            return False
        if self.scope == 'resources' and not (context.resource_tools and (context.allowed_file_ids or context.history_sources)):
            return False
        if self.scope == 'resource_files' and not (context.resource_tools and context.allowed_file_ids):
            return False
        return self.available is None or bool(self.available(context))

    def definition(self):
        schema = self.arguments.model_json_schema()
        schema.pop('title', None)
        return {'type': 'function', 'function': {'name': self.name, 'description': self.description, 'parameters': schema}}


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec):
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', spec.name) or spec.name in self._tools:
            raise ValueError(f'工具名称不合法或重复：{spec.name}')
        if spec.scope not in {'always', 'files', 'history', 'resources', 'resource_files'}:
            raise ValueError('未知的工具可用范围')
        if not issubclass(spec.arguments, ToolArguments) or not callable(spec.handler) or not spec.label.strip() or not spec.description.strip():
            raise ValueError('工具必须提供参数模型、执行函数、名称及说明')
        reserved = set(ToolContext.__dataclass_fields__)
        if reserved.intersection(spec.arguments.model_fields):
            raise ValueError('工具参数不能声明后端上下文的保留字段')
        if spec.available is not None and (not callable(spec.available) or inspect.iscoroutinefunction(spec.available)):
            raise ValueError('工具可用性判断必须是同步函数')
        if spec.offer_when is not None and (not callable(spec.offer_when) or inspect.iscoroutinefunction(spec.offer_when)):
            raise ValueError('工具清单条件必须是同步函数')
        if not 0 < spec.timeout <= 3600 or not 0 < spec.max_result_bytes <= 1048576:
            raise ValueError('工具超时或结果大小上限不合法')
        self._tools[spec.name] = spec
        return spec

    def definitions(self, context: ToolContext):
        return [spec.definition() for spec in self._tools.values() if spec.is_available(context) and (spec.offer_when is None or spec.offer_when(context))]

    def label(self, name: str):
        spec = self._tools.get(name)
        return spec.label if spec is not None else name

    async def execute(self, name: str, arguments: str, context: ToolContext) -> str:
        spec = self._tools.get(name)
        if spec is None:
            raise ValueError(f'工具不存在：{name}')
        try:
            parameters = json.loads(arguments)
        except (ValueError, TypeError):
            raise ValueError('工具参数必须是有效JSON对象') from None
        if not isinstance(parameters, dict):
            raise ValueError('工具参数必须是JSON对象')
        try:
            values = spec.arguments.model_validate(parameters).model_dump()
        except ValidationError as error:
            fields = ', '.join('.'.join(str(part) for part in item['loc']) for item in error.errors(include_input=False, include_url=False)[:3])
            raise ValueError(f'工具参数不合法，请检查字段：{fields}') from None
        try:
            with anyio.fail_after(spec.timeout):
                await context.refresh_access()
                if not spec.is_available(context):
                    raise ValueError('当前会话没有使用该工具所需的资料或权限')
                cache_key = (name, frozenset(context.allowed_file_ids), frozenset(context.history_sources), json.dumps(values, sort_keys=True, ensure_ascii=False))
                if spec.once_per_turn and cache_key in context.cache:
                    return context.cache[cache_key]
                if spec.once_per_turn and any(key[0] == name for key in context.cache):
                    raise ValueError('该工具本轮已执行，请沿用已有结果或在下一轮调整参数和资料范围')
                kwargs = {**values, 'context': context} if spec.uses_context else values
                if inspect.iscoroutinefunction(spec.handler):
                    result = await spec.handler(**kwargs)
                else:
                    result = await anyio.to_thread.run_sync(lambda: spec.handler(**kwargs), abandon_on_cancel=True)
                    if inspect.isawaitable(result):
                        result = await result
                try:
                    serialized = json.dumps(result, ensure_ascii=False, allow_nan=False)
                except (TypeError, ValueError):
                    raise ValueError('工具返回了无法保存的结果，请检查工具实现') from None
                if len(serialized.encode('utf-8')) > spec.max_result_bytes:
                    raise ValueError('工具结果过大，请缩小查询范围或使用分页读取')
                if spec.once_per_turn:
                    context.cache[cache_key] = serialized
                return serialized
        except TimeoutError:
            raise ValueError('工具执行超时，请稍后重试或缩小查询范围') from None


registry = ToolRegistry()


def register_tool(*, name, label, description, arguments=ToolArguments, **options):
    def decorate(handler):
        registry.register(ToolSpec(name=name, label=label, description=description, arguments=arguments, handler=handler, **options))
        return handler
    return decorate
