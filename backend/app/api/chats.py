from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends, Query, HTTPException, Response
from fastapi.responses import StreamingResponse

from backend.app.core.database import get_db
from backend.app.repositories.chat import get_latest_message,rename_chat,delete_chat,create_chat, list_chats, get_chat, list_messages,create_message,list_recent_messages
from backend.app.schemas.chat import ChatCreate, ChatResponse, MessageResponse,MessageCreate,ChatRename
from backend.app.chat.service import stream_chat_history_events
from backend.app.chat.response import ChatStreamingResponse  # 带生成器清理的聊天流式响应
from backend.app.chat.guards import acquire_generation, release_generation  # 占用和释放会话的生成资格


#创建一个接口分组
router = APIRouter(prefix="/api/chats",tags=["会话管理"])


@router.post("", response_model=ChatResponse, status_code=201)
async def create_chat_endpoint(request: ChatCreate,session: AsyncSession = Depends(get_db)):
    """创建会话并返回会话信息。"""

    chat = await create_chat(session=session,title=request.title)
    return chat
@router.get("", response_model=list[ChatResponse])
async def list_chats_endpoint(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
):
    """分页获取会话列表。"""

    return await list_chats(session=session,offset=offset,limit=limit)
@router.get("/{chat_id}", response_model=ChatResponse)
async def get_chat_endpoint(chat_id: str,session: AsyncSession = Depends(get_db)):
    """根据 ID 获取一个会话。"""
    chat = await get_chat(session=session,chat_id=chat_id)

    if chat is None:
        raise HTTPException(status_code=404,detail="会话不存在",)

    return chat
@router.get("/{chat_id}/messages",response_model=list[MessageResponse],)
async def list_messages_endpoint(
    chat_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_db)):
    """查询指定会话的消息列表。"""

    chat = await get_chat(session, chat_id)#对应会话id行的chat对象

    if chat is None:
        raise HTTPException(status_code=404,detail="会话不存在")

    return await list_messages(
        session=session,
        chat_id=chat_id,
        offset=offset,
        limit=limit,
    )
@router.post("/{chat_id}/messages",
    response_model=MessageResponse,
    status_code=201)
async def create_message_endpoint(
    chat_id: str,
    request: MessageCreate,
    session: AsyncSession = Depends(get_db),
):
    """保存一条用户消息，暂不生成助手回答。"""

    try:
        message = await create_message(
            session=session,
            chat_id=chat_id,
            role="user",
            content=request.content,
        )

    except ValueError:
        raise HTTPException(
            status_code=404,
            detail="会话不存在",
        )

    return message

@router.post("/{chat_id}/respond")
async def respond_endpoint(chat_id: str, session: AsyncSession = Depends(get_db)):  # 检查并占用生成资格，再返回回答流
    if not acquire_generation(chat_id):
        raise HTTPException(status_code=409, detail="该会话正在生成或清理中，请稍后重试")
    try:
        chat = await get_chat(session, chat_id)  # 查询目标会话
        if chat is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        latest = await get_latest_message(session, chat_id)  # 查询最新消息，不过滤状态
        if latest is None:
            raise HTTPException(status_code=409, detail="请先发送一条用户消息")
        if latest.role == "assistant":
            if latest.status == "generating":
                raise HTTPException(status_code=409, detail="回答仍在生成或清理中，请稍后重试")
            if latest.status not in ("failed", "cancelled"):
                raise HTTPException(status_code=409, detail="最新回答已完成，请发送新问题")
        elif latest.role != "user" or latest.status != "completed":
            raise HTTPException(status_code=409, detail="最新消息不支持生成回答")
        return ChatStreamingResponse(
            content=stream_chat_history_events(session=session, chat_id=chat_id),
            chat_id=chat_id,
        )
    except BaseException:
        release_generation(chat_id)  # 尚未交给响应对象就退出，由接口负责释放
        raise

@router.delete("/{chat_id}", status_code=204)
async def delete_chat_endpoint(chat_id: str, session: AsyncSession = Depends(get_db)):  # 删除指定会话及全部消息
    deleted = await delete_chat(session, chat_id)  # 执行删除事务
    if not deleted:
        raise HTTPException(status_code=404, detail="会话不存在")
    return Response(status_code=204)

@router.patch("/{chat_id}", response_model=ChatResponse)
async def rename_chat_endpoint(chat_id: str, request: ChatRename, session: AsyncSession = Depends(get_db)):  # 修改指定会话标题
    chat = await rename_chat(session, chat_id, request.title)  # 保存新标题
    if chat is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return chat