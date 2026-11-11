from typing import Any  # 工具定义中的值可以是字符串、字典等类型
from backend.app.tools.builtin import get_current_time  # 已有的时间函数
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.tools.files import read_uploaded_file, search_uploaded_files  # 读取与检索共用后端给定的附件范围
import json  # 解析模型参数，并将工具结果转成字符串

RESOURCE_TOOLS = [
    {"type": "function", "function": {"name": "list_available_files", "description": "列出当前会话可用文件和历史来源快照。总结资料前先确认范围；清单不含全文。", "parameters": {"type": "object", "properties": {"offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}, "additionalProperties": False}}},
    {"type": "function", "function": {"name": "read_history_sources", "description": "读取历史工具返回的原文快照，并取得本轮可引用编号。删除库或文件后仍可讨论这些已返回片段；不能获取快照以外的原文。", "parameters": {"type": "object", "properties": {"source_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 4}}, "required": ["source_ids"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "summarize_uploaded_files", "description": "分段读取并归纳文件原文，用于总结整份文件或整个知识库。省略file_ids处理当前全部文件；可指定文件子集。返回覆盖范围与原文来源，不完整时必须说明。每轮最多执行一次。", "parameters": {"type": "object", "properties": {"file_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 200}, "focus": {"type": "string", "maxLength": 1000}}, "additionalProperties": False}}},
]

TOOL_DEFINITIONS: list[dict[str, Any]] = [  # 发给模型看的工具说明
    {
        "type": "function",
        "function": {
            "name": "search_uploaded_files",
            "description": "按问题检索本次附件和当前会话已选知识库中的相关片段。查找条款、事实或具体信息时优先使用；省略file_id搜索允许的全部文件，指定时仅搜索该文件。结果携带文件名、切片ID和原文位置，不代表读完全文，distance不是正确率。索引不可用时明确说明，可改用read_uploaded_file读取；需要全文、上下文或完整汇总时使用原文读取工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": 1000, "description": "要在附件中查找的问题或信息"},
                    "file_id": {"type": "string", "description": "可选，本次附件或知识库检索结果中的文件ID；只能缩小搜索范围"},
                    "file_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 200, "description": "可选，限定多个允许文件；不能同时指定file_id"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10, "description": "最多返回的片段数，默认5"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
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
            "description": "读取本次附件或已选知识库文件的正文。回答涉及资料内容的问题前应先获取依据。按字符分页；has_more为true时可用next_offset继续读取，不能把一个片段当成全文。",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_id": {"type": "string", "description": "本次附件信息或search_uploaded_files结果提供的文件ID，不要猜测"},
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
    "search_uploaded_files": search_uploaded_files,
}

def get_available_tools(allowed_file_ids: set[str], has_history: bool = False, resource_tools: bool = False) -> list[dict[str, Any]]:
    tools = []
    for definition in TOOL_DEFINITIONS:
        if definition["function"]["name"] in ("read_uploaded_file", "search_uploaded_files") and not allowed_file_ids:
            continue
        tools.append(definition)
    if resource_tools and (allowed_file_ids or has_history):
        tools.append(RESOURCE_TOOLS[0])
        if has_history:
            tools.append(RESOURCE_TOOLS[1])
        if allowed_file_ids:
            tools.append(RESOURCE_TOOLS[2])
    return tools

async def execute_tool(name: str, arguments: str, session: AsyncSession, allowed_file_ids: set[str]) -> str:  # 兼容同步时间函数和异步文件读取
    function = TOOL_FUNCTIONS.get(name)  # 只允许执行注册表中的函数
    if function is None:
        raise ValueError(f"工具不存在：{name}")
    parameters = json.loads(arguments)  # 将模型返回的 JSON 字符串转换成字典
    if not isinstance(parameters, dict):
        raise ValueError("工具参数必须是 JSON 对象")
    if name in ("read_uploaded_file", "search_uploaded_files"):
        allowed = {"file_id", "offset", "max_chars"} if name == "read_uploaded_file" else {"query", "file_id", "file_ids", "limit"}
        required = "file_id" if name == "read_uploaded_file" else "query"
        if required not in parameters:
            raise ValueError(f"工具缺少必要参数：{required}")
        extra = set(parameters) - allowed  # 不允许模型覆盖数据库连接或允许的文件范围
        if extra:
            raise ValueError("附件工具参数不合法")
        result = await function(session=session, allowed_file_ids=allowed_file_ids, **parameters)
    else:
        result = function(**parameters)  # 当前时间函数仍是普通同步函数
    return json.dumps(result, ensure_ascii=False)  # 转成后续工具结果消息的正文
