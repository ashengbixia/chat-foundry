"""账号工具的共享权限与持久化实现；模型不指定账号。"""
import time
import posixpath
from sqlalchemy import select, update, delete, func
from backend.app.models.memory import Memory, UserToolSetting
from backend.app.models.user import User


DEFAULT_FEATURES = {'memory_enabled': False, 'history_enabled': True, 'knowledge_enabled': True, 'files_enabled': True, 'follow_ups_enabled': True}


async def load_features(session, account_id):
    row = await session.get(UserToolSetting, account_id)
    return {key: getattr(row, key) if row else value for key, value in DEFAULT_FEATURES.items()}


def account_available(feature):
    return lambda ctx: bool(ctx.account_id and not ctx.is_subagent and ctx.account_features.get(feature, DEFAULT_FEATURES[feature]))


async def require_account(ctx, feature):
    if not account_available(feature)(ctx):
        raise ValueError('该账号工具未开启，或当前子任务没有账号范围权限')
    user = await ctx.session.get(User, ctx.account_id, populate_existing=True)
    features = await load_features(ctx.session, ctx.account_id)
    if user is None or user.role == 'disabled' or not features[feature]:
        raise ValueError('账号权限已撤销或工具已关闭')
    return ctx.account_id


def normalize_path(value):
    value = (value or '/').strip()
    if '\\' in value or any(ord(ch) < 32 for ch in value) or '..' in value.split('/'):
        raise ValueError('记忆路径不能包含上级目录或控制字符')
    value = posixpath.normpath('/' + value.lstrip('/'))
    if len(value) > 300:
        raise ValueError('记忆路径过长')
    return value


def memory_view(row):
    return {key: getattr(row, key) for key in ('id', 'content', 'path', 'type', 'revision', 'created_at', 'updated_at')}


async def find_memories(session, account_id, query='', type='all', path=None, memory_id=None, offset=0, count=20):
    statement = select(Memory).where(Memory.user_id == account_id)
    if query:
        statement = statement.where(Memory.content.contains(query, autoescape=True) | Memory.path.contains(query, autoescape=True))
    if type != 'all':
        statement = statement.where(Memory.type == type)
    if path is not None:
        path = normalize_path(path)
        statement = statement.where((Memory.path == path) | Memory.path.startswith(path.rstrip('/') + '/', autoescape=True))
    if memory_id:
        statement = statement.where(Memory.id == memory_id)
    rows = list((await session.execute(statement.order_by(Memory.updated_at.desc(), Memory.id).offset(offset).limit(count + 1))).scalars())
    return {'memories': [memory_view(row) for row in rows[:count]], 'has_more': len(rows) > count, 'next_offset': offset + count if len(rows) > count else None}


async def write_memory(session, account_id, content, path='/', type='user', memory_id=None, revision=None):
    content = content.strip()
    if not content or len(content) > 8000:
        raise ValueError('记忆正文应为1至8000字符')
    path = normalize_path(path)
    now = int(time.time())
    if memory_id is None:
        # A tiny write transaction serializes the per-account quota check.
        await session.execute(update(User).where(User.id == account_id).values(name=User.name))
        duplicate = await session.scalar(select(Memory).where(Memory.user_id == account_id, Memory.content == content, Memory.path == path, Memory.type == type))
        if duplicate:
            await session.commit()
            return memory_view(duplicate)
        if (await session.scalar(select(func.count()).select_from(Memory).where(Memory.user_id == account_id))) >= 1000:
            await session.commit()
            raise ValueError('记忆已达1000条，请先整理或删除旧记忆')
        row = Memory(user_id=account_id, content=content, path=path, type=type)
        session.add(row)
        await session.commit()
        return memory_view(row)
    result = await session.execute(update(Memory).where(Memory.id == memory_id, Memory.user_id == account_id, Memory.revision == revision).values(
        content=content, path=path, type=type, revision=Memory.revision + 1, updated_at=now))
    if not result.rowcount:
        await session.commit()
        raise ValueError('记忆不存在、无权限或版本已变化，请重新读取')
    await session.commit()
    return memory_view(await session.get(Memory, memory_id, populate_existing=True))


async def remove_memory(session, account_id, memory_id, revision):
    result = await session.execute(delete(Memory).where(Memory.id == memory_id, Memory.user_id == account_id, Memory.revision == revision))
    if not result.rowcount:
        await session.commit()
        raise ValueError('记忆不存在、无权限或版本已变化，请重新读取')
    await session.commit()
    return {'deleted': True, 'id': memory_id}
