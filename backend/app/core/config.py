from pathlib import Path

from pydantic import Field, SecretStr
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):  # 优先读取环境变量，本机开发时也可读取 backend/.env
    app_env: Literal['development', 'production'] = 'development'
    data_dir: Path = Path(__file__).resolve().parents[2] / 'data'
    registration_enabled: bool = True
    bootstrap_token: SecretStr = Field(default='', repr=False)
    model_internal_urls: list[str] = Field(default_factory=list)  # 运维明确授权的完整API基础地址，不能填写网段或通配符
    model_global_concurrency: int = Field(default=8, ge=1)
    model_user_concurrency: int = Field(default=3, ge=1)
    model_queue_timeout: float = Field(default=30, gt=0)
    index_concurrency: int = Field(default=1, ge=1)
    index_queue_limit: int = Field(default=100, ge=1)
    chat_global_concurrency: int = Field(default=32, ge=1)
    chat_user_concurrency: int = Field(default=6, ge=1)
    user_storage_bytes: int = Field(default=104857600, ge=1)
    total_storage_bytes: int = Field(default=10737418240, ge=1)
    user_file_count: int = Field(default=200, ge=1)
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
    chat_response_max_tool_call_iterations: int = Field(default=1024, ge=-1)  # 0禁用模型工具迭代，-1不限制；自动资料准备另计。
    embedding_model: str = Field(default="BAAI/bge-small-zh-v1.5", min_length=1)  # 向量模型名称，也可以填写本地模型目录
    embedding_device: str = Field(default="cpu", min_length=1)  # 初版默认使用CPU
    embedding_query_prefix: str = "为这个句子生成表示以用于检索相关文章："  # 当前BGE模型的查询提示，更换模型时按需调整或清空
    retrieval_max_distance: float = Field(default=0.75, ge=0, le=2)  # 超出距离的纯语义候选不作为资料返回
    web_search_enabled: bool = True
    web_browser_enabled: bool = False  # 兼容旧配置；显式true且未指定loader时使用auto。
    web_loader_engine: Literal['', 'safe_web', 'playwright', 'auto'] = 'safe_web'
    web_url_policy: Literal['public', 'sources'] = 'public'
    web_fetch_max_content_length: int | None = Field(default=None, ge=0)  # 0也表示不截短，兼容Open WebUI语义。
    web_fetch_max_pages: int | None = Field(default=None, ge=1)
    subagents_enabled: bool = True
    subagent_timeout: int = Field(default=300, ge=10, le=1800)
    web_search_provider: Literal['duckduckgo', 'bing', 'tavily', 'brave'] = 'bing'
    web_search_api_key: SecretStr = Field(default='', repr=False)  # 与模型服务Key完全分开
    web_search_result_count: int = Field(default=3, ge=1, le=100)
    web_search_max_attempts: int | None = Field(default=None, ge=1)
    auth_cookie_secure: bool = False  # 本机HTTP开发为false，HTTPS部署应设为true
    auth_session_days: int = Field(default=7, ge=1, le=30)  # 登录会话最长有效天数
    model_config_encryption_key: SecretStr = Field(default="", repr=False)  # 可选Fernet主密钥；未配置时在本机私密目录生成


# 创建配置对象，其他文件通过导入 settings 使用配置。
settings = Settings()
