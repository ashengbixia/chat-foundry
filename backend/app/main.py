from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel,Field
from backend.app.llm.client import generate_reply
from backend.app.chat.service import stream_chat_events
from contextlib import asynccontextmanager
import logging  # 记录启动恢复结果

from backend.app.core.database import SessionLocal, engine, init_db  # 数据库会话、引擎和初始化方法
from backend.app.repositories.chat import recover_interrupted_messages  # 恢复上次中断的消息状态

logger = logging.getLogger(__name__)  # 当前模块日志

@asynccontextmanager
async def lifespan(app: FastAPI):  # 管理应用启动和关闭
    try:
        await init_db()  # 创建尚不存在的数据库表
        async with SessionLocal() as session:  # 启动恢复使用独立数据库会话
            recovered_count = await recover_interrupted_messages(session)  # 处理上次遗留的生成中消息
        if recovered_count > 0:
            logger.warning("已将 %s 条中断的助手消息标记为失败", recovered_count)
        yield  # 启动准备完成，开始接收请求
    finally:
        await engine.dispose()  # 退出时释放数据库连接

from backend.app.api.chats import router as chats_router
app = FastAPI(title=" AI 助手",lifespan=lifespan,)
app.include_router(chats_router)#把接口分组注册到 FastAPI 应用上

class ChatRequest(BaseModel):
    """聊天接口接收的数据。"""
    message: str = Field(min_length=1, max_length=10000)
@app.get("/health")
def health_check():
    """检查后端是否正常运行。"""
    return {"status": "ok"}
@app.post('/api/chat')
async def chat(request:ChatRequest):
    reply=await generate_reply(request.message)
    return {"reply":reply}
@app.post("/api/chat/stream")
async def chat_stream(request: ChatRequest):
    """把模型生成的正文片段持续发送给客户端。"""
    return StreamingResponse(
        stream_chat_events(request.message),
        media_type="text/event-stream",   #media_type="text/plain; charset=utf-8",纯正文流式
        headers={"Cache-Control": "no-cache"},

    )
