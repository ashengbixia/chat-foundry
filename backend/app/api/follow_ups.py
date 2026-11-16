from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker
from backend.app.auth import get_current_user, require_chat_owner
from backend.app.core.database import get_db
from backend.app.api.model_settings import get_user_model_connection
from backend.app.chat import follow_ups

router = APIRouter(prefix='/api/chats/{chat_id}/messages/{message_id}/follow-ups', tags=['推荐追问'], dependencies=[Depends(require_chat_owner)])


@router.get('')
async def state(chat_id: str, message_id: str, user=Depends(get_current_user), session=Depends(get_db)):
    try:
        _, result = await follow_ups.get_state(session, chat_id, message_id, user.id)
        return result
    except ValueError:
        raise HTTPException(404, '消息不存在') from None


@router.post('')
async def generate(chat_id: str, message_id: str, retry: bool = False, user=Depends(get_current_user), session=Depends(get_db)):
    try:
        row, current = await follow_ups.get_state(session, chat_id, message_id, user.id)
        if current['status'] in ('completed', 'disabled', 'skipped', 'generating') or (current['status'] == 'failed' and not retry):
            return current
        connection = await get_user_model_connection(user, session)
        return await follow_ups.start(session, async_sessionmaker(session.bind, expire_on_commit=False), chat_id, message_id, user.id, connection, retry)
    except ValueError:
        raise HTTPException(404, '消息不存在') from None
