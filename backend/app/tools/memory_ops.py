"""长期记忆工具共享实现。"""
from sqlalchemy import select, func
from backend.app.models.memory import Memory
from backend.app.tools.account import require_account, find_memories, write_memory, remove_memory, normalize_path


async def search(ctx, query='', count=5, type='all', path=None, memory_id=None, offset=0):
    owner = await require_account(ctx, 'memory_enabled')
    result = await find_memories(ctx.session, owner, query, type, path, memory_id, offset, count)
    if not memory_id:
        for row in result['memories']:
            row['total_chars'] = len(row['content'])
            row['content_truncated'] = len(row['content']) > 1000
            row['content'] = row['content'][:1000]
        result['notice'] = '长记忆仅返回前1000字符；content_truncated为true时用search_memories并指定memory_id读取完整正文后再整体修改。'
    return result


async def paths(ctx, query='', count=100, type='all', offset=0):
    owner = await require_account(ctx, 'memory_enabled')
    statement = select(Memory.path, func.count()).where(Memory.user_id == owner)
    if query:
        statement = statement.where(Memory.path.contains(query, autoescape=True) | Memory.content.contains(query, autoescape=True))
    if type != 'all':
        statement = statement.where(Memory.type == type)
    rows = (await ctx.session.execute(statement.group_by(Memory.path).order_by(Memory.path).offset(offset).limit(count + 1))).all()
    return {'paths': [{'path': path, 'count': total} for path, total in rows[:count]], 'has_more': len(rows) > count, 'next_offset': offset + count if len(rows) > count else None}


async def add(ctx, content, type='user', path='/'):
    owner = await require_account(ctx, 'memory_enabled')
    return await write_memory(ctx.session, owner, content, path, type)


async def update(ctx, memory_id, revision, content, type=None, path=None):
    owner = await require_account(ctx, 'memory_enabled')
    row = await ctx.session.scalar(select(Memory).where(Memory.id == memory_id, Memory.user_id == owner))
    if row is None:
        raise ValueError('记忆不存在或无权限')
    return await write_memory(ctx.session, owner, content, path if path is not None else row.path, type if type is not None else row.type, memory_id, revision)


async def replace(ctx, memory_id, revision, old_content, new_content):
    owner = await require_account(ctx, 'memory_enabled')
    row = await ctx.session.scalar(select(Memory).where(Memory.id == memory_id, Memory.user_id == owner))
    if row is None or row.revision != revision or row.content.count(old_content) != 1:
        raise ValueError('记忆不存在、版本变化或替换文字不是唯一匹配，请重新读取')
    return await write_memory(ctx.session, owner, row.content.replace(old_content, new_content), row.path, row.type, row.id, revision)


async def remove(ctx, memory_id, revision):
    owner = await require_account(ctx, 'memory_enabled')
    return await remove_memory(ctx.session, owner, memory_id, revision)
