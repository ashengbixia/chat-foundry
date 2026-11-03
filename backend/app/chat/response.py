import logging  # 记录资源清理异常
import anyio  # 为清理操作提供超时和取消保护
from starlette.responses import StreamingResponse  # 基础流式响应
from starlette.types import Receive, Scope, Send  # ASGI 调用参数类型
from backend.app.chat.guards import release_generation  # 释放会话的生成资格
logger = logging.getLogger(__name__)  # 当前模块日志


class ChatStreamingResponse(StreamingResponse):  # 在响应退出时关闭聊天生成器
    def __init__(self, content, chat_id: str):  # 创建响应对象，并记住它属于哪个会话
        super().__init__(content, media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
        self.chat_id = chat_id  # 把会话 ID 保存在当前响应对象中

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:  # 发送响应，退出后清理并解锁
        try:
            await super().__call__(scope, receive, send)  # 原有的流式发送过程
        finally:
            try:
                close_stream = getattr(self.body_iterator, "aclose", None)  # 获取生成器关闭方法
                if close_stream is not None:
                    with anyio.move_on_after(10, shield=True) as cleanup_scope:
                        try:
                            await close_stream()  # 原来的取消清理逻辑
                        except Exception:
                            logger.exception("关闭聊天生成器失败")
                    if cleanup_scope.cancel_called:
                        logger.warning("关闭聊天生成器超时")
            finally:
                release_generation(self.chat_id)  # 无论清理成功还是失败，都释放生成资格