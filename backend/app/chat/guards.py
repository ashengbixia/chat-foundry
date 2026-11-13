_active_generations: set[str] = set()  # 当前进程中已占用生成资格的会话 ID
_owners: dict[str, str] = {}


def acquire_generation(chat_id: str, user_id: str | None = None) -> bool:  # 尝试占用会话的生成资格
    if chat_id in _active_generations:
        return False
    if user_id is not None:
        from fastapi import HTTPException
        from backend.app.core.config import settings
        if len(_owners) >= settings.chat_global_concurrency or sum(owner == user_id for owner in _owners.values()) >= settings.chat_user_concurrency:
            raise HTTPException(429, '同时运行的会话已达限额，请等待或停止其他回答', headers={'Retry-After': '10'})
        _owners[chat_id] = user_id
    _active_generations.add(chat_id)
    return True


def release_generation(chat_id: str) -> None:  # 释放会话的生成资格
    _active_generations.discard(chat_id)
    _owners.pop(chat_id, None)
