from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):  # 优先读取环境变量，本机开发时也可读取 backend/.env
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )
    # 模型服务的接口地址
    llm_base_url: str = ""
    # 模型服务的密钥；打印配置对象时不显示它
    llm_api_key: SecretStr = Field(default="", validation_alias="DEEPSEEK_API_KEY", repr=False)  # 从 DEEPSEEK_API_KEY 读取，内部字段名保持不变
    # 要调用的模型名称
    llm_model: str = ""
    # 单次模型请求的超时时间，单位：秒
    llm_timeout: float = Field(default=60.0, gt=0)
    llm_context_window: int = Field(default=32768, gt=0)  # 应用采用的总预算，不代表模型官方上限
    llm_max_output_tokens: int = Field(default=4096, gt=0)  # 输出上限，同时传给模型服务
    llm_context_margin: int = Field(default=1024, ge=0)  # 为包装和估算误差预留空间


# 创建配置对象，其他文件通过导入 settings 使用配置。
settings = Settings()
