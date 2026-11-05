from typing import Any  # 工具定义中的值可以是字符串、字典等类型
from backend.app.tools.builtin import get_current_time  # 已有的时间函数
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.tools.files import read_uploaded_file  # 按字符分页读取附件
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
    {
        "type": "function",
        "function": {
            "name": "read_uploaded_file",
            "description": "读取用户附件正文。回答涉及附件内容的问题前应先读取资料。按字符分页；has_more为true时可用next_offset继续读取，不能把一个片段当成全文。",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_id": {"type": "string", "description": "用户消息中提供的附件ID"},
                    "offset": {"type": "integer", "minimum": 0, "maximum": 2147483647, "description": "起始字符位置，默认0"},
                    "max_chars": {"type": "integer", "minimum": 1, "maximum": 12000, "description": "本次读取字符数，默认6000"},
                },
                "required": ["file_id"],
                "additionalProperties": False,
            },
        },
    },
]

TOOL_FUNCTIONS = {  # 后端允许执行的工具名称与函数的对应关系
    "get_current_time": get_current_time,
    "read_uploaded_file": read_uploaded_file,
}

def get_available_tools(allowed_file_ids: set[str]) -> list[dict[str, Any]]:  # 没有可用附件时不提供读取工具
    tools = []
    for definition in TOOL_DEFINITIONS:
        if definition["function"]["name"] == "read_uploaded_file" and not allowed_file_ids:
            continue
        tools.append(definition)
    return tools

async def execute_tool(name: str, arguments: str, session: AsyncSession, allowed_file_ids: set[str]) -> str:  # 兼容同步时间函数和异步文件读取
    function = TOOL_FUNCTIONS.get(name)  # 只允许执行注册表中的函数
    if function is None:
        raise ValueError(f"工具不存在：{name}")
    parameters = json.loads(arguments)  # 将模型返回的 JSON 字符串转换成字典
    if not isinstance(parameters, dict):
        raise ValueError("工具参数必须是 JSON 对象")
    if name == "read_uploaded_file":
        extra = set(parameters) - {"file_id", "offset", "max_chars"}  # 不允许模型覆盖数据库连接或允许的文件范围
        if extra:
            raise ValueError("读取文件的工具参数不合法")
        result = await function(session=session, allowed_file_ids=allowed_file_ids, **parameters)
    else:
        result = function(**parameters)  # 当前时间函数仍是普通同步函数
    return json.dumps(result, ensure_ascii=False)  # 转成后续工具结果消息的正文
