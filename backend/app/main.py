from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel,Field
from backend.app.llm.client import generate_reply
from backend.app.chat.service import stream_chat_events
from contextlib import asynccontextmanager

from backend.app.core.database import engine, init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    """管理后端启动和关闭时的资源。"""
    try:
        # 启动阶段：创建尚不存在的数据库表。
        await init_db()
        # 启动完成，应用开始接收请求。
        yield
    finally:
        # 关闭阶段：释放数据库连接池。
        await engine.dispose()

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
