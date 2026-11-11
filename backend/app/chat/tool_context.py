import json
from backend.app.llm.budget import estimate_input_tokens, get_input_budget

def project_tool_results(messages, raw_results, tools, context_window, output_tokens, margin):  # 只压缩模型视图，数据库与前端仍保留完整工具结果
    projected = []
    bodies = []
    seen = set()
    names = {}
    for message in messages:
        for call in message.get("tool_calls", []):
            names[call["id"]] = call.get("function", {}).get("name")
        call_id = message.get("tool_call_id")
        if message.get("role") != "tool" or call_id not in raw_results or names.get(call_id) not in ("search_uploaded_files", "read_uploaded_file", "read_history_sources", "summarize_uploaded_files"):
            projected.append(message)
            continue
        try:
            data = json.loads(raw_results[call_id])
        except (ValueError, TypeError):
            projected.append(message)
            continue
        if not isinstance(data, dict):
            projected.append(message)
            continue
        if names[call_id] in ("search_uploaded_files", "read_history_sources", "summarize_uploaded_files") and isinstance(data.get("chunks"), list):
            unique, duplicates = [], []
            for chunk in data["chunks"]:
                if not isinstance(chunk, dict) or not isinstance(chunk.get("content"), str):
                    unique.append(chunk)
                    continue
                identity = (chunk.get("file_id"), chunk.get("chunk_id"), chunk.get("start_offset"), chunk.get("end_offset"), chunk["content"])
                if identity in seen:
                    duplicates.append({key: chunk[key] for key in ("file_id", "chunk_id", "start_offset", "end_offset") if key in chunk})
                else:
                    seen.add(identity)
                    unique.append(chunk)
                    bodies.append((chunk, "content", data))
                    if isinstance(chunk.get("summary"), str):
                        bodies.append((chunk, "summary", data))
            data["chunks"] = unique
            if duplicates:
                data["omitted_duplicate_chunks"] = duplicates
                data["context_notice"] = "重复正文已在本轮较早工具结果中返回，请引用此前内容；此处省略不表示没有命中。"
        elif names[call_id] == "read_uploaded_file" and isinstance(data.get("text_content"), str):
            bodies.append((data, "text_content", data))
        view = dict(message)
        projected.append(view)
        view["content"] = data  # 循环结束后统一序列化，避免修改原消息

    def serialize():
        return [dict(message, content=json.dumps(message["content"], ensure_ascii=False, separators=(",", ":"))) if isinstance(message.get("content"), dict) else message for message in projected]

    budget = get_input_budget(context_window, output_tokens, margin)
    while True:
        result = serialize()
        latest_user = max((index for index, item in enumerate(result) if item.get("role") == "user"), default=0)
        required = [item for item in result[:latest_user] if item.get("role") == "system"] + result[latest_user:]
        if estimate_input_tokens(required, tools) <= budget:
            return result
        adjustable = [body for body in bodies if len(body[0][body[1]]) > 64]
        if not adjustable:
            return result  # 剩余必要输入由现有预算检查明确报错，不能删除问题或工具调用
        item, field, parent = max(adjustable, key=lambda body: len(body[0][body[1]]))
        item[field] = item[field][:max(64, len(item[field]) // 2)]
        item["truncated"] = True
        parent["truncated"] = True
        parent["context_limited"] = True
        parent["context_notice"] = "部分正文因本轮输入预算被截短，不能把片段当成完整文件；必要时按偏移读取。"
        if field == "content" and isinstance(item.get("start_offset"), int):
            item["end_offset"] = item["start_offset"] + len(item[field])
        if field == "text_content":
            item["next_offset"] = item.get("offset", item.get("start_offset", 0)) + len(item[field])
            item["has_more"] = True
