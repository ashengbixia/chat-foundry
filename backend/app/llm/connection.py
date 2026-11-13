from dataclasses import dataclass, field
from pydantic import SecretStr

@dataclass(frozen=True)
class ModelConnection:  # 一次回答固定使用同一份配置，其他用户保存设置不会影响本轮请求
    base_url: str
    model: str
    timeout: float
    api_key: SecretStr = field(repr=False)  # 配置对象的日志和异常表示中不包含密钥
    allow_no_key: bool = False  # 只有用户明确选择无需Key时，才允许不携带认证头
    user_id: str | None = None  # 限额归属，子任务和自动标题继承同一用户
