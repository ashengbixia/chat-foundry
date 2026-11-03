_active_generations: set[str] = set()  # 当前进程中已占用生成资格的会话 ID


def acquire_generation(chat_id: str) -> bool:  # 尝试占用会话的生成资格
    if chat_id in _active_generations:
        return False
    _active_generations.add(chat_id)
    return True


def release_generation(chat_id: str) -> None:  # 释放会话的生成资格
    _active_generations.discard(chat_id)
