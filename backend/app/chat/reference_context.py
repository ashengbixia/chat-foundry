import json
import re
from sqlalchemy import func, select
from backend.app.models.file import File
from backend.app.core.config import settings
from backend.app.llm.budget import estimate_input_tokens, get_input_budget
from backend.app.tools.files import read_uploaded_file
from backend.app.repositories.resources import describe_resources
from backend.app.chat.history_sources import collect_history_sources, read_history_sources


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
                    focused = {item.get('file_id') for item in collect_history_sources([message]).values()} & allowed_ids
                    if len(focused) == 1:
                        targets = focused
                    break
        return targets, f'{question[:500]}\n上一个问题：{previous}'
    return allowed_ids, question


async def prepare_auto_reference(session, question, allowed_ids, messages, tools, connection, check_access, history_sources=None, history=None):
    if not is_reference_question(question):
        return []
    if re.search(r'https?://', question, re.I):
        return []  # 新链接交给网页工具，不自动用旧资料快照冒充当前网页
    if not allowed_ids:
        return []  # 历史只提供目录，由模型结合最近意图按需选择，不自动灌入旧正文。
    catalog = await describe_resources(session, allowed_ids)
    users = [item for item in (history or []) if item.role == 'user']
    attached_now = bool(users and getattr(users[-1], 'attachments', []))
    explicit = bool(re.search(r'附件|文件|资料|知识库|文档|手册|刚才|上面|这份|这段|继续', question))
    named = any(item['filename'].rsplit('.', 1)[0] in question for item in catalog)
    if not (attached_now or explicit or named):
        return []  # 当前资料可用不等于每个新话题都需要检索，模型仍可按需调用。
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
