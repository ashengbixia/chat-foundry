"""委派工具的运行后端；主子运行复用service，各自使用独立Session。"""
import asyncio
import hashlib
import json
import time
import uuid
import weakref
from dataclasses import dataclass
import anyio
from sqlalchemy import select, update, func
from backend.app.core.config import settings
from backend.app.models.chat import Chat, Message
from backend.app.models.user import User
from backend.app.models.file import File
from backend.app.models.chat_file import ChatFile
from backend.app.models.subagent import SubagentTask
from backend.app.repositories.resources import resolve_chat_files
from backend.app.repositories.knowledge import resolve_knowledge_scope
from backend.app.chat.guards import acquire_generation, release_generation

ACTIVE = ('queued', 'running', 'cancelling')
TASK_TOOLS = frozenset({'delegate_task', 'get_subagent_task', 'cancel_subagent_task'})
_jobs = {}
_deliveries = {}
_delivery_wakes = {}
_locks = weakref.WeakValueDictionary()
_slots = weakref.WeakKeyDictionary()
_closing = False


@dataclass
class ChildScope:
    task_id: str
    parent_chat_id: str
    files: set[str]
    history_sources: dict
    tools: frozenset[str]
    urls: frozenset[str]
    sessions: object


def _semaphore(key, count):
    loop = asyncio.get_running_loop()
    limits = _slots.setdefault(loop, {})
    return limits.setdefault(key, asyncio.Semaphore(count))


async def file_scope(session, chat_id):
    _, knowledge = await resolve_knowledge_scope(session, chat_id)
    return await resolve_chat_files(session, chat_id) | knowledge


async def current_child_scope(session, scope):
    task = await session.get(SubagentTask, scope.task_id, populate_existing=True)
    parent = await session.get(Chat, scope.parent_chat_id, populate_existing=True)
    user = await session.get(User, task.user_id, populate_existing=True) if task else None
    if not task or not parent or not user or user.role == 'disabled' or task.status not in ACTIVE:
        raise ValueError('子任务已结束、取消或所属会话不可访问')
    scope.files &= await file_scope(session, scope.parent_chat_id)
    return set(scope.files)


def task_view(task):
    return {key: getattr(task, key) for key in ('id', 'parent_chat_id', 'parent_message_id', 'child_chat_id', 'call_id', 'task', 'background', 'status', 'error', 'delivery_status', 'created_at', 'started_at', 'finished_at')} | {
        'outcome': task.scope.get('outcome', 'failed' if task.status in ('failed', 'cancelled', 'timed_out', 'interrupted') else 'unknown'),
        'error_info': task.scope.get('error_info')}


async def owned_task(session, parent_chat_id, task_id):
    task = await session.get(SubagentTask, task_id)
    parent = await session.get(Chat, parent_chat_id)
    if not task or not parent or task.parent_chat_id != parent_chat_id or task.user_id != parent.user_id:
        raise ValueError('子任务不存在或不属于当前会话')
    return task


async def task_result(sessions, parent_chat_id, task_id):
    from backend.app.chat.history_sources import SOURCE_TOOLS
    async with sessions() as session:
        task = await owned_task(session, parent_chat_id, task_id)
        data = task_view(task)
        data['task_id'] = data.pop('id')
        if not data['error']:
            data.pop('error')
        data.update(summary='', chunks=[])
        message = await session.get(Message, task.result_message_id) if task.result_message_id else None
        if message is None:
            return data
        from backend.app.chat.research import assess_result
        data.update(assess_result(message))
        data['task'] = task.task[:180]
        data['summary'] = message.content[:1200]
        data['result_truncated'] = len(message.content) > 1200
        names, seen, remaining = {}, set(), 2400
        for item in message.output or []:
            if item.get('type') == 'function_call':
                names[item.get('call_id')] = item.get('name')
            elif item.get('type') == 'function_call_output' and names.get(item.get('call_id')) in SOURCE_TOOLS:
                try:
                    result = json.loads(item['output'])
                    chunks = [result] if names[item['call_id']] == 'read_uploaded_file' else result.get('chunks', [])
                    for chunk in chunks:
                        number = chunk.get('citation_id')
                        content = chunk.get('content', chunk.get('text_content'))
                        start = chunk.get('start_offset', chunk.get('offset'))
                        if type(number) is not int or number in seen or not isinstance(content, str) or type(start) is not int:
                            continue
                        seen.add(number)
                        if remaining <= 0:
                            data['evidence_truncated'] = True
                            continue
                        quota = min(800, remaining)
                        snapshot = {**chunk, 'content': content[:quota], 'start_offset': start,
                                    'end_offset': start + len(content[:quota]), 'child_citation_id': number,
                                    'origin_message_id': message.id, 'origin_created_at': message.created_at,
                                    'historical': True, 'task_id': task.id, 'child_chat_id': task.child_chat_id,
                                    'truncated': bool(chunk.get('truncated')) or len(content) > quota}
                        snapshot.pop('citation_id', None)
                        snapshot.pop('text_content', None)  # 读取工具原字段不能绕过摘要证据长度限制。
                        data['chunks'].append(snapshot)
                        if snapshot['truncated']:
                            data['evidence_truncated'] = True
                        remaining -= len(snapshot['content'])
                except (ValueError, KeyError, TypeError, AttributeError):
                    continue
        return data


async def delegate(context, task, task_context='', background=False, file_ids=None, history_source_ids=None, tool_names=None):
    from backend.app.tools.runtime import registry
    from backend.app.tools.local.read_webpage import approved_urls, normalize_url
    if context.is_subagent or not context.session_factory or not context.parent_message_id:
        raise ValueError('仅主运行可以委派子任务')
    task = task.strip()
    if not task:
        raise ValueError('子任务不能为空')
    selected = set(file_ids or [])
    if not selected <= context.allowed_file_ids:
        raise ValueError('子任务只能使用当前会话明确允许的文件')
    source_ids = set(history_source_ids or [])
    if not source_ids <= set(context.history_sources):
        raise ValueError('历史来源不存在')
    available = {tool['function']['name'] for tool in registry.definitions(context)} - TASK_TOOLS
    tools = set(tool_names) if tool_names is not None else available
    if not tools <= available:
        raise ValueError('子任务不能增加主运行未开放的工具')
    import re
    urls = set()
    allowed_urls = approved_urls(context, context.state.get('read_webpage', {}))
    for url in re.findall(r'https?://[^\s<>"\x27]+', task + '\n' + task_context):
        try:
            normalized = str(normalize_url(url.rstrip(').,，。；;')))
            if normalized not in allowed_urls:
                raise ValueError('委派中的网址必须来自用户提供、真实搜索结果或已读页面')
            urls.add(normalized)
        except ValueError:
            raise ValueError('委派中的网址未获得父会话授权') from None
    scope = {'files': sorted(selected), 'history_sources': {key: context.history_sources[key] for key in source_ids},
             'tools': sorted(tools), 'urls': sorted(urls), 'web_search_enabled': context.web_search_enabled}
    request_key = hashlib.sha256(json.dumps([task, task_context, background, scope], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    lock = _locks.setdefault('submission', asyncio.Lock())  # 短事务串行登记，账号额度跨父会话一致。
    async with lock:
        async with context.session_factory() as session:
            parent = await session.get(Chat, context.chat_id)
            owner = await session.get(User, parent.user_id) if parent and parent.user_id else None
            message = await session.get(Message, context.parent_message_id)
            if not parent or parent.kind != 'conversation' or not owner or owner.role == 'disabled' or not message or message.chat_id != parent.id or message.status != 'generating':
                raise ValueError('主会话不可用，无法创建子任务')
            selected &= await file_scope(session, parent.id)
            if selected != set(scope['files']):
                raise ValueError('分配的资料已从父会话移除')
            existing = (await session.execute(select(SubagentTask).where(SubagentTask.parent_message_id == message.id, SubagentTask.request_key == request_key))).scalar_one_or_none()
            if existing:
                task_id = existing.id
            else:
                count = (await session.execute(select(func.count()).select_from(SubagentTask).where(SubagentTask.parent_message_id == message.id))).scalar_one()
                active = (await session.execute(select(func.count()).select_from(SubagentTask).where(SubagentTask.user_id == parent.user_id, SubagentTask.status.in_(ACTIVE)))).scalar_one()
                if count >= 4 or active >= 8:
                    raise ValueError('本轮最多4个子任务，同账号最多8个待完成子任务')
                child_id, task_id = str(uuid.uuid4()), str(uuid.uuid4())
                child = Chat(id=child_id, user_id=parent.user_id, title='子任务：' + task[:25], title_status='manual', kind='subagent', web_search_enabled=context.web_search_enabled)
                session.add(child)
                await session.flush()
                files = list((await session.execute(select(File).where(File.id.in_(selected)))).scalars()) if selected else []
                session.add_all(ChatFile(chat_id=child_id, file_id=file.id, linked_file_id=file.id, filename=file.filename, size=file.size) for file in files)
                session.add(Message(chat_id=child_id, sequence=1, role='user', content=task + ('\n\n任务背景：\n' + task_context if task_context else ''), status='completed'))
                session.add(SubagentTask(id=task_id, user_id=parent.user_id, parent_chat_id=parent.id, parent_message_id=message.id,
                    child_chat_id=child_id, call_id=context.call_id or '', request_key=request_key, task=task,
                    background=background, scope=scope, status='queued', delivery_status='none'))
                await session.commit()
                _launch(context.session_factory, task_id, context.connection)
    job = _jobs.get(task_id)
    if not background and job is not None:
        try:
            await job
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():
                raise
    return await task_result(context.session_factory, context.chat_id, task_id)


def _launch(sessions, task_id, connection):
    global _closing
    _closing = False
    job = asyncio.create_task(_run(sessions, task_id, connection))
    _jobs[task_id] = job
    def finished(done):
        _jobs.pop(task_id, None)
        if not done.cancelled():
            done.exception()  # 任务状态由_run持久保存，不向事件循环泄漏未取回异常。
    job.add_done_callback(finished)


async def _run(sessions, task_id, connection):
    from backend.app.chat.service import stream_chat_history_events
    async with sessions() as session:
        task = await session.get(SubagentTask, task_id)
        if not task:
            return
        parent_id, child_id, user_id, background, data = task.parent_chat_id, task.child_chat_id, task.user_id, task.background, task.scope
    status, error, result_id, cancelled = 'failed', None, None, False
    worker_error_info = None
    try:
        with anyio.fail_after(60):
            await _semaphore(('user', user_id), 2).acquire()
        try:
            with anyio.fail_after(60):
                await _semaphore('global', 4).acquire()
            try:
                async with sessions() as session:
                    changed = await session.execute(update(SubagentTask).where(SubagentTask.id == task_id, SubagentTask.status == 'queued').values(status='running', started_at=int(time.time())))
                    await session.commit()
                    if not changed.rowcount:
                        return
                scope = ChildScope(task_id, parent_id, set(data['files']), data['history_sources'], frozenset(data['tools']), frozenset(data['urls']), sessions)
                with anyio.fail_after(settings.subagent_timeout):
                    async with sessions() as session:
                        async for _ in stream_chat_history_events(session, child_id, connection, data['web_search_enabled'], run_scope=scope):
                            pass
                        result = (await session.execute(select(Message).where(Message.chat_id == child_id, Message.role == 'assistant').order_by(Message.sequence.desc()).limit(1))).scalar_one_or_none()
                        if result:
                            result_id = result.id
                            status = 'completed' if result.status == 'completed' else 'failed'
                            error = result.error if status == 'failed' else None
                        else:
                            error = '子任务没有产生回答'
            finally:
                _semaphore('global', 4).release()
        finally:
            _semaphore(('user', user_id), 2).release()
    except asyncio.CancelledError:
        status, error = ('interrupted', '后端停止，子任务已中断') if _closing else ('cancelled', '子任务已停止')
        worker_error_info = {'phase': 'subagent_run', 'code': status, 'retryable': False}
        cancelled = True
    except TimeoutError:
        status, error = 'timed_out', '子任务排队或执行超时'
        worker_error_info = {'phase': 'subagent_run', 'code': 'subagent_timeout', 'retryable': True}
    except Exception as exception:
        from backend.app.chat.research import failure_info
        worker_error_info, _ = failure_info(exception, 'subagent_run')
        status, error = 'failed', '子任务执行失败，请查看子会话历史'
    async with sessions() as session:
        # 失败/取消时同样关联已保存的部分结果。
        if result_id is None:
            result_id = (await session.execute(select(Message.id).where(Message.chat_id == child_id, Message.role == 'assistant').order_by(Message.sequence.desc()).limit(1))).scalar_one_or_none()
        from backend.app.chat.research import assess_result
        result_message = await session.get(Message, result_id) if result_id else None
        assessment = assess_result(result_message)
        error_info = next((item.get('error_info') for item in reversed(result_message.output or []) if item.get('type') == 'turn_metadata'), None) if result_message else None
        error_info = worker_error_info or error_info
        await session.execute(update(SubagentTask).where(SubagentTask.id == task_id).values(status=status, error=error,
            result_message_id=result_id, finished_at=int(time.time()), delivery_status='pending' if background else 'none', scope={**data, **assessment, 'error_info': error_info}))
        await session.commit()
    if background:
        kick_delivery(sessions, parent_id, connection)
    if cancelled:
        raise asyncio.CancelledError


async def cancel_task(sessions, parent_id, task_id):
    async with sessions() as session:
        task = await owned_task(session, parent_id, task_id)
        if task.status not in ACTIVE:
            return task_view(task)
        await session.execute(update(SubagentTask).where(SubagentTask.id == task_id, SubagentTask.status.in_(ACTIVE)).values(status='cancelling'))
        await session.commit()
    job = _jobs.get(task_id)
    if job is not None:
        job.cancel()
        await asyncio.gather(job, return_exceptions=True)
    async with sessions() as session:
        await session.execute(update(SubagentTask).where(SubagentTask.id == task_id, SubagentTask.status == 'cancelling').values(status='cancelled', finished_at=int(time.time()), error='子任务已停止'))
        await session.commit()
    return await task_result(sessions, parent_id, task_id)


def kick_delivery(sessions, parent_id, connection):
    if _closing:
        return
    existing = _deliveries.get(parent_id)
    if existing is not None and not existing.done():
        _delivery_wakes[parent_id].set()
        return
    wake = asyncio.Event()
    _delivery_wakes[parent_id] = wake
    async def drain():
        while True:
            wake.clear()
            await _deliver(sessions, parent_id, connection)
            if not wake.is_set():
                return
    job = asyncio.create_task(drain())
    _deliveries[parent_id] = job
    def finished(done):
        if _deliveries.get(parent_id) is done:
            _deliveries.pop(parent_id, None)
        if _delivery_wakes.get(parent_id) is wake:
            _delivery_wakes.pop(parent_id, None)
        if not done.cancelled():
            done.exception()
    job.add_done_callback(finished)


async def _deliver(sessions, parent_id, connection):
    from backend.app.chat.service import stream_chat_history_events
    from backend.app.repositories.chat import get_latest_message
    while True:
        await asyncio.sleep(.25)
        if not acquire_generation(parent_id):
            continue
        ids = []
        try:
            async with sessions() as session:
                parent = await session.get(Chat, parent_id)
                owner = await session.get(User, parent.user_id) if parent else None
                if not parent or not owner or owner.role == 'disabled':
                    if parent:
                        await session.execute(update(SubagentTask).where(SubagentTask.parent_chat_id == parent_id, SubagentTask.delivery_status == 'pending').values(delivery_status='available'))
                        await session.commit()
                    return
                latest = await get_latest_message(session, parent_id)
                if latest and (latest.role == 'user' or latest.status == 'generating'):
                    continue
                ids = list((await session.execute(select(SubagentTask.id).where(SubagentTask.parent_chat_id == parent_id, SubagentTask.delivery_status == 'pending').limit(4))).scalars())
                if not ids:
                    return
                await session.execute(update(SubagentTask).where(SubagentTask.id.in_(ids)).values(delivery_status='delivering'))
                await session.commit()
                async for _ in stream_chat_history_events(session, parent_id, connection, parent.web_search_enabled, continuation_task_ids=ids):
                    pass
                # 引擎完成落库后才确认投递；失败保留可查看结果，不循环自动计费重试。
                latest = await get_latest_message(session, parent_id)
                delivered = latest and latest.status == 'completed'
                await session.execute(update(SubagentTask).where(SubagentTask.id.in_(ids), SubagentTask.delivery_status == 'delivering').values(delivery_status='delivered' if delivered else 'available'))
                await session.commit()
        finally:
            if ids:
                async with sessions() as session:
                    await session.execute(update(SubagentTask).where(SubagentTask.id.in_(ids), SubagentTask.delivery_status == 'delivering').values(delivery_status='available'))
                    await session.commit()
            release_generation(parent_id)


async def pause_delivery(parent_id):
    job = _deliveries.get(parent_id)
    if job is not None:
        job.cancel()
        await asyncio.gather(job, return_exceptions=True)


async def stop_parent(sessions, parent_id, foreground_only=False, parent_message_id=None):
    if not foreground_only:
        await pause_delivery(parent_id)
    async with sessions() as session:
        query = select(SubagentTask.id).where(SubagentTask.parent_chat_id == parent_id, SubagentTask.status.in_(ACTIVE))
        if foreground_only:
            query = query.where(SubagentTask.background.is_(False), SubagentTask.parent_message_id == parent_message_id)
        ids = list((await session.execute(query)).scalars())
    for task_id in ids:
        await cancel_task(sessions, parent_id, task_id)


async def close_tasks():
    global _closing
    _closing = True
    jobs = list(_deliveries.values()) + list(_jobs.values())
    for job in jobs:
        job.cancel()
    if jobs:
        await asyncio.gather(*jobs, return_exceptions=True)


async def recover_tasks(session):
    global _closing
    _closing = False
    tasks = list((await session.execute(select(SubagentTask).where(SubagentTask.status.in_(ACTIVE)))).scalars())
    for task in tasks:
        result = (await session.execute(select(Message).where(Message.chat_id == task.child_chat_id, Message.role == 'assistant').order_by(Message.sequence.desc()).limit(1))).scalar_one_or_none()
        task.status = 'completed' if result and result.status == 'completed' else 'interrupted'
        task.error = None if task.status == 'completed' else '后端重启，任务已中断；未自动重试'
        task.result_message_id = result.id if result else None
        task.finished_at = int(time.time())
        if task.background:
            task.delivery_status = 'available'
    await session.execute(update(SubagentTask).where(SubagentTask.delivery_status.in_(['pending', 'delivering'])).values(delivery_status='available'))
    await session.commit()
