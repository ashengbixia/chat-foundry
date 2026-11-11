import json

FILE_TOOLS = {"search_uploaded_files", "read_uploaded_file", "read_history_sources", "summarize_uploaded_files"}


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
            if item.get("type") != "function_call_output" or name not in FILE_TOOLS:
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
                if not isinstance(text, str) or not text.strip() or type(offset) is not int or offset < 0 or type(chunk.get("citation_id")) is not int or not isinstance(chunk.get("file_id"), str) or not isinstance(chunk.get("filename"), str):
                    continue
                identity = (chunk["file_id"], offset, text)
                if identity in seen:
                    continue
                seen.add(identity)
                source_id = f"H{len(sources) + 1}"
                sources[source_id] = {"source_id": source_id, "file_id": chunk["file_id"], "filename": chunk["filename"], "content": text, "start_offset": offset, "end_offset": offset + len(text), "historical": True, "origin_message_id": chunk.get("origin_message_id") or getattr(message, "id", None), "origin_created_at": chunk.get("origin_created_at") or getattr(message, "created_at", None), "truncated": bool(chunk.get("truncated"))}
                if len(sources) >= 40:
                    return sources
    return sources


def history_source_catalog(sources: dict) -> list[dict]:
    return [{key: item[key] for key in ("source_id", "filename", "file_id", "origin_message_id", "origin_created_at", "start_offset", "end_offset")} | {"excerpt": item["content"][:100]} for item in sources.values()]


def read_history_sources(source_ids: list[str], sources: dict) -> dict:
    if not isinstance(source_ids, list) or not 1 <= len(source_ids) <= 4 or any(not isinstance(key, str) or key not in sources for key in source_ids):
        raise ValueError("请从历史来源清单选择1至4个有效source_id")
    result, remaining = [], 5000
    for key in dict.fromkeys(source_ids):
        item = dict(sources[key])
        text = item["content"][:remaining]
        if not text:
            break
        item.update(content=text, end_offset=item["start_offset"] + len(text), truncated=item["truncated"] or len(text) < len(item["content"]))
        result.append(item)
        remaining -= len(text)
    return {"chunks": result, "notice": "这些是历史工具返回的原文快照，不是重新读取当前文件；只可依据已返回的片段作答，不能推断未读部分。"}
