"""只查询后端绑定会话中的证据快照，不接收模型传入的用户或会话 ID。"""
import re
from sqlalchemy import select
from backend.app.models.chat import Message
from backend.app.chat.history_sources import collect_history_sources, history_source_catalog


def bind_history_lookup(session, chat_id):
    async def lookup(source_ids=None, before_sequence=None, query='', limit=4):
        statement = select(Message).where(Message.chat_id == chat_id, Message.role == 'assistant', Message.status == 'completed')
        if source_ids:
            parsed = [re.fullmatch(r'M(\d+):(\d+)', value) for value in source_ids]
            if not all(parsed):
                raise ValueError('历史来源编号无效，请先查询历史目录')
            statement = statement.where(Message.sequence.in_([int(match[1]) for match in parsed]))
        elif before_sequence is not None:
            statement = statement.where(Message.sequence < before_sequence)
        rows = list((await session.execute(statement.order_by(Message.sequence.desc()).limit(limit))).scalars())
        sources = {}
        for row in rows:
            for index, source in enumerate(collect_history_sources([row]).values(), 1):
                key = f'M{row.sequence}:{index}'
                source = dict(source, source_id=key)
                if source_ids and key not in source_ids:
                    continue
                if query and query.casefold() not in (source['filename'] + '\n' + source['content']).casefold():
                    continue
                sources[key] = source
        if source_ids and set(source_ids) != set(sources):
            raise ValueError('所选快照不存在于当前会话，请重新查询历史目录')
        return sources, {'sources': history_source_catalog(sources),
                         'next_before_sequence': rows[-1].sequence if len(rows) == limit else None,
                         'notice': '当前会话历史快照目录；query仅筛选本页，继续使用next_before_sequence查找更早内容。摘要不是原文。'}
    return lookup
