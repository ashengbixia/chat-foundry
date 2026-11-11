import json

def add_citation_ids(name: str, result: str, sources: dict, allowed_file_ids: set[str]) -> str:  # 只给本轮实际返回、且在允许范围内的附件正文编号
    if name not in ("search_uploaded_files", "read_uploaded_file"):
        return result
    try:
        data = json.loads(result)
    except (ValueError, TypeError):
        return result
    if not isinstance(data, dict) or "error" in data:
        return result
    items = data.get("chunks", []) if name == "search_uploaded_files" else [data]
    if not isinstance(items, list):
        return result
    changed = False
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("file_id"), str) or item["file_id"] not in allowed_file_ids:
            continue
        content = item.get("content" if name == "search_uploaded_files" else "text_content")
        start = item.get("start_offset" if name == "search_uploaded_files" else "offset")
        if not isinstance(content, str) or not content.strip() or type(start) is not int or start < 0 or not isinstance(item.get("filename"), str):
            continue
        identity = (item["file_id"], start, content)
        if identity not in sources:
            sources[identity] = len(sources) + 1
        item["citation_id"] = sources[identity]
        changed = True
    return json.dumps(data, ensure_ascii=False) if changed else result
