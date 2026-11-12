"""查询当前北京时间，不依赖模型或外部服务。"""
from datetime import datetime, timedelta, timezone
from backend.app.tools.runtime import ToolArguments, register_tool


class Arguments(ToolArguments):
    pass


@register_tool(name='get_current_time', label='查询当前时间', description='获取当前北京时间。用户询问当前日期、时间或星期时可以调用。', arguments=Arguments, timeout=5)
def get_current_time() -> dict[str, str | int]:
    now = datetime.now(timezone(timedelta(hours=8)))
    return {'datetime': now.isoformat(timespec='seconds'), 'timezone': 'Asia/Shanghai', 'timestamp': int(now.timestamp())}
