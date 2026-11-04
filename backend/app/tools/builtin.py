from datetime import datetime, timedelta, timezone  # 使用标准库获取带时区的时间


def get_current_time() -> dict[str, str | int]:  # 获取当前北京时间，不需要模型或外部服务
    beijing_timezone = timezone(timedelta(hours=8))  # 固定 UTC+8，不依赖服务器所在时区
    now = datetime.now(beijing_timezone)  # 本次调用的当前时间
    return {
        "datetime": now.isoformat(timespec="seconds"),
        "timezone": "Asia/Shanghai",
        "timestamp": int(now.timestamp()),
    }
