import json
from backend.app.chat.source_types import is_web_source

SOURCE_TOOLS = {"search_uploaded_files", "read_uploaded_file", "read_history_sources", "summarize_uploaded_files", "read_webpage", 'delegate_task', 'get_subagent_task'}


def collect_history_sources(history: list) -> dict[str, dict]:  # 只从当前会话真实工具结果提取证据，不能把助手自己的说法当作原文
    sources, seen = {}, set()
    for message in reversed(history):
        if message.role != "assistant" or message.status != "completed":
            continue
        calls = {}
        for item in getattr(message, "output", []) or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "function_call":
                if isinstance(item.get("call_id"), str) and isinstance(item.get("name"), str):
                    calls[item["call_id"]] = item["name"]
                continue
            if not isinstance(item.get("call_id"), str):
                continue
            name = calls.get(item.get("call_id"))
            if item.get("type") != "function_call_output" or name not in SOURCE_TOOLS:
                continue
            try:
                data = json.loads(item["output"])
            except (ValueError, TypeError, KeyError):
                continue
            if not isinstance(data, dict) or "error" in data:
                continue
            chunks = [data] if name == "read_uploaded_file" else data.get("chunks", [])
            if not isinstance(chunks, list):
                continue
            for chunk in chunks:
                if not isinstance(chunk, dict):
                    continue
                text = chunk.get("text_content" if name == "read_uploaded_file" else "content")
                offset = chunk.get("offset" if name == "read_uploaded_file" else "start_offset")
                web = is_web_source(chunk) and name in ('read_webpage', 'read_history_sources', 'delegate_task', 'get_subagent_task')
                if not isinstance(text, str) or not text.strip() or type(offset) is not int or offset < 0 or type(chunk.get("citation_id")) is not int or (not web and (not isinstance(chunk.get("file_id"), str) or not isinstance(chunk.get("filename"), str))):
                    continue
                identity = ('web', chunk['url'], offset, text) if web else (chunk["file_id"], offset, text)
                if identity in seen:
                    continue
                seen.add(identity)
                source_id = f"H{len(sources) + 1}"
                identity_fields = {'source_type': 'web', 'url': chunk['url'], 'title': chunk['title'], 'filename': chunk['title'], 'retrieved_at': chunk.get('retrieved_at')} if web else {'file_id': chunk['file_id'], 'filename': chunk['filename']}
                sources[source_id] = {"source_id": source_id, **identity_fields, "content": text, "start_offset": offset, "end_offset": offset + len(text), "historical": True, "origin_message_id": chunk.get("origin_message_id") or getattr(message, "id", None), "origin_created_at": chunk.get("origin_created_at") or getattr(message, "created_at", None), "truncated": bool(chunk.get("truncated"))}
                if len(sources) >= 40:
                    return sources
    return sources


def history_source_catalog(sources: dict) -> list[dict]:
    return [{key: item[key] for key in ("source_id", "filename", "file_id", "source_type", "url", "title", "retrieved_at", "origin_message_id", "origin_created_at", "start_offset", "end_offset") if key in item} | {"excerpt": item["content"][:100]} for item in sources.values()]


def read_history_sources(source_ids: list[str], sources: dict, offset=0, max_chars=5000) -> dict:
    if not isinstance(source_ids, list) or not 1 <= len(source_ids) <= 4 or any(not isinstance(key, str) or key not in sources for key in source_ids):
        raise ValueError("请从历史来源清单选择1至4个有效source_id")
    if type(offset) is not int or offset < 0 or type(max_chars) is not int or not 1 <= max_chars <= 12000:
        raise ValueError('历史快照读取位置或长度不合法')
    result, pages = [], []
    keys = list(dict.fromkeys(source_ids))
    quota = max_chars // len(keys)
    for index, key in enumerate(keys):
        item = dict(sources[key])
        length = quota + (1 if index < max_chars % len(keys) else 0)
        text = item["content"][offset:offset + length]
        end = offset + len(text)
        pages.append({'source_id': key, 'offset': offset, 'returned': bool(text), 'total_chars': len(item['content']),
                      'has_more': end < len(item['content']), 'next_offset': end if end < len(item['content']) else None})
        if not text:
            continue
        item.update(content=text, start_offset=item['start_offset'] + offset,
                    end_offset=item["start_offset"] + end,
                    truncated=item["truncated"] or end < len(item["content"]))
        result.append(item)
    return {"chunks": result, 'pages': pages, "notice": "这些是历史工具返回的原文快照，不是重新读取当前文件；多来源均分正文预算，pages列出各自返回范围。继续阅读时选单个source_id并使用该项next_offset；只可依据已返回片段作答。"}
