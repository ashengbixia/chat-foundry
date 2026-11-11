import json
import re
from sqlalchemy import func, select
from backend.app.models.file import File
from backend.app.core.config import settings
from backend.app.llm.budget import estimate_input_tokens, get_input_budget
from backend.app.tools.files import read_uploaded_file
from backend.app.tools.summaries import summarize_uploaded_files
from backend.app.repositories.resources import describe_resources
from backend.app.chat.history_sources import collect_history_sources, history_source_catalog, read_history_sources


def is_reference_question(question: str) -> bool:
    simple = re.sub(r"[\s，。！？,.!?]", "", question).lower()
    return simple not in {"你好", "您好", "hi", "hello", "谢谢", "好的", "嗯", "现在几点", "现在几点了", "今天几号", "今天星期几"}


def resolve_reference_scope(question, allowed_ids, catalog, history):
    named = {item['file_id'] for item in catalog if item['filename'] in question or item['filename'].rsplit('.', 1)[0] in question}
    if named:
        return named, question
    users = [item for item in (history or []) if item.role == 'user']
    attached = {item.file_id for item in getattr(users[-1], 'attachments', [])} & allowed_ids if users else set()
    broad = bool(re.search(r'全部|所有|整个知识库|对比|比较|两者|分别|另一个', question))
    if attached and not broad:
        return attached, question
    # 只为明确指代的追问补充上文，普通新问题仍检索全部当前资料。
    if re.search(r'^(那|它|这个|这份|刚才|上面|继续|为什么)', question) and len(users) >= 2:
        previous = users[-2].content[:160]
        targets = allowed_ids
        if not broad:
            for message in reversed(history or []):
                if message.role == 'assistant' and message.status == 'completed':
                    focused = {item['file_id'] for item in collect_history_sources([message]).values()} & allowed_ids
                    if len(focused) == 1:
                        targets = focused
                    break
        return targets, f'{question[:500]}\n上一个问题：{previous}'
    return allowed_ids, question


async def prepare_auto_reference(session, question, allowed_ids, messages, tools, connection, check_access, history_sources=None, history=None):
    if not is_reference_question(question):
        return []
    if not allowed_ids:
        if not history_sources:
            return []
        terms = {question[index:index + 2] for index in range(len(question) - 1) if not question[index:index + 2].isspace()}
        ranked = sorted(history_sources, key=lambda key: sum(term in (history_sources[key]['filename'] + history_sources[key]['content']) for term in terms), reverse=True)
        arguments = {'source_ids': ranked[:4]}
        return [('read_history_sources', arguments, read_history_sources(arguments['source_ids'], history_sources))]
    catalog = await describe_resources(session, allowed_ids)
    targets, query = resolve_reference_scope(question, allowed_ids, catalog, history)
    rows = (await session.execute(select(File.id, File.status, func.length(File.text_content).label("chars")).where(File.id.in_(sorted(targets))))).all()
    budget = get_input_budget(settings.llm_context_window, settings.llm_max_output_tokens, settings.llm_context_margin)
    latest = max((i for i, item in enumerate(messages) if item.get("role") == "user"), default=0)
    required = [item for item in messages[:latest] if item.get("role") == "system"] + messages[latest:]
    # 小资料先尝试全文；最终把工具包装也计入预算，不能只看字符数。
    if rows and len(rows) <= 10 and all(row.status == "completed" for row in rows) and sum(row.chars for row in rows) <= 6000:
        reads, view = [], list(required)
        for row in rows:
            current = await check_access()
            if row.id not in current:
                continue
            data = await read_uploaded_file(row.id, session, current, max_chars=max(1, row.chars))
            arguments = {"file_id": row.id, "max_chars": max(1, row.chars)}
            call_id = f"prefetch-{len(reads)}"
            view.extend([{"role": "assistant", "content": None, "tool_calls": [{"id": call_id, "type": "function", "function": {"name": "read_uploaded_file", "arguments": json.dumps(arguments)}}]}, {"role": "tool", "tool_call_id": call_id, "content": json.dumps(data, ensure_ascii=False)}])
            reads.append(("read_uploaded_file", arguments, data))
        if reads and estimate_input_tokens(view, tools) + 1024 < budget:
            return reads
    if re.search(r"总结|概括|汇总|梳理|里面.*(什么|内容)|主要.*(讲|内容)|summari[sz]e", question, re.IGNORECASE):
        arguments = {"focus": question[:1000], "file_ids": sorted(targets)}
        return [("summarize_uploaded_files", arguments, None)]
    arguments = {"query": query[:1000], **({"file_ids": sorted(targets)} if targets != allowed_ids else {})}
    return [("search_uploaded_files", arguments, None)]


async def execute_reference_tool(name, arguments, session, allowed_ids, history_sources, connection, check_access):
    parameters = json.loads(arguments)
    if not isinstance(parameters, dict):
        raise ValueError("工具参数必须是JSON对象")
    fields = {"list_available_files": {"offset", "limit"}, "read_history_sources": {"source_ids"}, "summarize_uploaded_files": {"file_ids", "focus"}}[name]
    if set(parameters) - fields:
        raise ValueError("资料工具参数不合法")
    if name == "read_history_sources":
        return read_history_sources(parameters.get("source_ids"), history_sources)
    if name == "summarize_uploaded_files":
        return await summarize_uploaded_files(session, allowed_ids, connection, **parameters, check_access=check_access)
    offset, limit = parameters.get("offset", 0), parameters.get("limit", 20)
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("清单offset必须为非负整数，limit必须为1至50的整数")
    files = await describe_resources(session, allowed_ids)
    history = history_source_catalog(history_sources)
    return {"files": files[offset:offset + limit], "total_files": len(files), "has_more": offset + limit < len(files), "history_sources": history[offset:offset + limit], "total_history_sources": len(history), "history_has_more": offset + limit < len(history), "notice": "files是当前可读取原文，history_sources仅代表以前返回过的快照；两者不能混为一谈。"}
