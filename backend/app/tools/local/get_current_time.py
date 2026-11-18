"""查询当前北京时间，不依赖模型或外部服务。"""
from datetime import datetime, timezone as utc_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pydantic import Field
from backend.app.tools.runtime import ToolArguments, register_tool


class Arguments(ToolArguments):
    timezone: str = Field(default='Asia/Shanghai', max_length=100, description='IANA时区名称，如Asia/Shanghai、UTC')


@register_tool(name='get_current_time', label='查询当前时间', description='获取当前Unix时间、UTC时间和指定时区的本地时间。默认Asia/Shanghai；用户明确其他时区时传IANA名称。', arguments=Arguments, timeout=5)
def get_current_time(timezone='Asia/Shanghai') -> dict[str, str | int]:
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError('时区不存在，请使用有效IANA时区名称') from None
    now = datetime.now(utc_timezone.utc)
    return {'datetime': now.astimezone(zone).isoformat(timespec='seconds'), 'utc_datetime': now.isoformat(timespec='seconds'), 'timezone': timezone, 'timestamp': int(now.timestamp())}
