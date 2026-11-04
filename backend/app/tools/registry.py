from typing import Any  # 工具定义中的值可以是字符串、字典等类型
from backend.app.tools.builtin import get_current_time  # 已有的时间函数
import json  # 解析模型参数，并将工具结果转成字符串

TOOL_DEFINITIONS: list[dict[str, Any]] = [  # 发给模型看的工具说明
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "获取当前北京时间。用户询问当前日期、时间或星期时可以调用。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
    },
]

TOOL_FUNCTIONS = {  # 后端允许执行的工具名称与函数的对应关系
    "get_current_time": get_current_time,
}

def execute_tool(name: str, arguments: str) -> str:  # 根据工具名和完整参数执行工具
    function = TOOL_FUNCTIONS.get(name)  # 只允许执行注册表中的函数
    if function is None:
        raise ValueError(f"工具不存在：{name}")
    parameters = json.loads(arguments)  # 将模型返回的 JSON 字符串转换成字典
    if not isinstance(parameters, dict):
        raise ValueError("工具参数必须是 JSON 对象")
    result = function(**parameters)  # 将参数字典展开后，调用实际函数
    return json.dumps(result, ensure_ascii=False)  # 转成后续工具结果消息的正文
