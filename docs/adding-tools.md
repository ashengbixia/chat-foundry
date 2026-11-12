# 新增工具

项目内置工具统一放在 `backend/app/tools/local/`：一个工具一个模块，文件名、注册name和处理函数名一致；每个模块包含自己的 `Arguments` 参数模型、`@register_tool` 声明和处理函数。local表示项目本地代码，不表示工具全部离线；联网搜索和网页读取也遵守同一结构。新增后在 `backend/app/tools/registry.py` 显式导入即可，无需修改聊天调度或前端中文名称表。加载新模块需要后端重启，开发模式的reload会自动重启。这不是页面安装插件或MCP热插拔功能。

当前模块：`get_current_time.py`、`read_uploaded_file.py`、`search_uploaded_files.py`、`list_available_files.py`、`read_history_sources.py`、`summarize_uploaded_files.py`、`read_webpage.py`、`search_web.py`，以及子任务入口 `delegate_task.py`、结果查询 `get_subagent_task.py`、停止 `cancel_subagent_task.py`。顶层的 `files.py`、`summaries.py`、`browser_reader.py` 是可复用业务实现，不自行注册工具；自动会话标题位于chat层，仍是后台任务。

## 1. 定义参数和处理函数

例如创建 `backend/app/tools/local/convert_temperature.py`：

```python
from pydantic import Field
from backend.app.tools.runtime import ToolArguments, register_tool


class Arguments(ToolArguments):
    celsius: float = Field(
        ge=-273.15, le=10000,
        description='摄氏温度',
    )


@register_tool(
    name='convert_temperature',
    label='转换温度',
    description='将摄氏温度转换为华氏温度。',
    arguments=Arguments,
    timeout=5,
)
def convert_temperature(celsius: float):
    return {'celsius': celsius, 'fahrenheit': round(celsius * 9 / 5 + 32, 2)}
```

这个例子只用于说明接入方法，没有默认启用。参数模型自动生成模型看到的JSON Schema，并在执行前进行同一份规则校验；多余字段、类型错误、超出范围都拒绝。普通同步函数在线程中执行，异步函数直接await。处理函数返回可JSON序列化的数据，不需要自己json.dumps。

## 2. 显式加载

在 `backend/app/tools/registry.py` 的内置模块导入后增加：

```python
from backend.app.tools.local import convert_temperature
```

注册器会拒绝重复名称或不合法配置。不扫描上传目录，不执行用户提供的Python代码；只有这里显式导入的受信任模块才会加载。

## 3. 使用及展示

重启后，用支持工具调用的聊天模型提问“25摄氏度是多少华氏度”。模型可以发现并调用该工具，工具结果继续进入现有模型循环、SSE事件和数据库output。中文label由后端随调用发送和保存，历史回答也可还原；普通JSON结果直接使用通用展示。需要专门的地图、图表或文件引用卡片时，仍需编写对应展示或引用适配。

## 涉及会话资料的工具

声明 `uses_context=True`，执行器就会注入后端的 `context`：

```python
@register_tool(
    name='inspect_resources',
    label='检查资料',
    description='返回当前会话允许资料的数量。',
    scope='files',
    uses_context=True,
)
async def inspect_resources(context):
    return {'file_count': len(context.allowed_file_ids)}
```

context包含当前数据库session、chat_id、允许文件集合、历史快照、模型连接及访问复核回调，也包含从数据库读取的会话搜索开关 `web_search_enabled`。这些是后端上下文，不能由模型参数声明或注入。执行前会重新检查范围，生成开始后只能撤销文件访问，不能通过模型参数扩大范围；具体file_id仍须在业务函数内验证是否属于允许集合。`search_web` 的可用性同时检查服务器总开关与会话开关；模型清单及执行入口使用同一规则。

子运行还包含后端注入的is_subagent、allowed_tools和approved_urls，均为保留字段。工具清单和实际执行同时检查工具白名单；子任务管理工具禁止在子运行使用。父回答中同批delegate_task会并发启动，各自使用独立Session与授权复核回调；普通工具仍按原顺序运行，不能直接共享Session后用gather并发所有工具。

需要单轮缓存时使用 `context.state`，按工具名称分区；它与执行器的单次调用复用缓存分开，且不会跨用户、会话或回答共享。已接入的 `read_webpage` 就通过这个状态保存当前回答抓取的网页正文，使多次分页读取基于同一份内容。

可用范围：`always`为普通工具；`files`要求当前有允许文件；`resources`要求启用资料工具且存在文件或历史快照；`history`要求启用资料工具且存在历史快照；`resource_files`要求启用资料工具且存在当前文件。范围同时控制模型清单及实际执行，伪造工具调用不能绕过检查。可用 `available=lambda context: ...` 增加同步可用性判断；其他业务权限仍需按当前身份验证，不等同于给模型授予权限。

额度随调用变化时可加 `offer_when=lambda context: ...`，决定是否继续出现在模型清单中；执行函数仍须检查额度，不能仅依赖清单过滤。`search_web` 在三次尝试后隐藏，伪造或同批次追加的调用仍被执行入口拒绝，缓存命中可复用已有成功结果。

## 执行约束

- 默认超时30秒、JSON结果上限256KiB，可通过注册配置调整；不截断原文冒充完整结果。超时、参数错误和超限会反馈给模型，意外异常记录后端日志并返回统一提示。
- 异步取消会继续传播。同步函数超时或取消后会停止等待，Python不能强制终止已经运行的线程；网络IO优先使用支持取消和自身超时的异步客户端，带事务的数据库操作使用异步处理函数，不在线程里复用AsyncSession。
- `once_per_turn=True`只允许一次回答内执行一次：相同参数和相同资料范围复用成功结果，更换参数或范围要求下一轮再执行；缓存不跨用户、会话或回答。
- 文件总结已迁入上述机制，使用600秒总超时；原来的分段及模型调用次数限制继续保留。
- 此次没有修改数据库表，展示名称保存在已有output JSON内，无需数据库升级。

代码入口：`runtime.py`负责统一注册与执行，`local/<工具名>.py`声明各个内置工具，`registry.py`显式加载模块并提供聊天使用的接口。旧的集中声明文件catalog.py及builtin.py已拆分移除，不保留两套注册入口。聊天流程只负责发出调用、调用统一执行器和保存结果。工具名称、参数Schema、中文名称、权限、超时及历史记录格式保持兼容。

`read_webpage` 的静态正文为空时会调用 `browser_reader.py`，在独立线程中启动隔离Chromium并等待动态正文。浏览器请求通过后端固定公网IP连接转发，不使用 `route.continue_()` 或浏览器自行发起的网络连接；新增浏览器能力时需保留这一边界。安装步骤及资源限制见README“网页读取”。浏览器结果沿用原有正文分页和引用快照，无需新增工具或数据库表。
