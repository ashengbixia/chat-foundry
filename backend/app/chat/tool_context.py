import json
from backend.app.llm.budget import estimate_input_tokens, get_input_budget
from backend.app.llm.budget import InputBudgetError
from backend.app.chat.context import trim_model_messages


def prepare_model_request(messages, raw_results, tools, context_window, output_tokens, margin, finish_only=False):
    """保留供应商要求的思考与调用配对，只压缩资料视图和完整旧轮次。"""
    from backend.app.chat.research import exhausted_research
    reason = 'previous_final_phase' if finish_only else 'research_exhausted' if exhausted_research(raw_results) else ''
    candidates = [None] if reason else [tools, None] if tools else [None]
    for candidate in candidates:
        working = list(messages)
        final = candidate is None
        if final and raw_results:
            working.insert(0, {'role': 'system', 'content': '现在仅汇总已取得的结果，不再调用工具。区分执行完成与证据充分，资料不足时说明缺口，不编造结论。'})
        try:
            projected = project_tool_results(working, raw_results, candidate, context_window, output_tokens, margin)
            trimmed = trim_model_messages(projected, candidate, context_window, output_tokens, margin)
        except InputBudgetError:
            if final:
                raise
            reason = 'input_budget'
            continue
        budget = get_input_budget(context_window, output_tokens, margin)
        if candidate and raw_results and estimate_input_tokens(trimmed, candidate) > budget - min(2048, budget // 8):
            reason = 'synthesis_reserve'
            continue
        return trimmed, candidate, final, reason
    raise InputBudgetError('本轮必要输入超过预算，无法继续请求模型')


def remember_visible_web_ranges(messages, raw_results, context):
    # 去重按模型实际看见的正文计数，不能用数据库中保留的完整版阻断预算截短后的续读。
    ranges = context.state.setdefault('read_webpage_visible_ranges', {})
    names = {call['id']: call.get('function', {}).get('name') for item in messages for call in item.get('tool_calls', [])}
    for item in messages:
        call_id = item.get('tool_call_id')
        if call_id not in raw_results or names.get(call_id) != 'read_webpage':
            continue
        try:
            data = json.loads(item['content'])
            for chunk in data.get('chunks', []):
                if isinstance(chunk.get('url'), str) and type(chunk.get('start_offset')) is int and isinstance(chunk.get('content'), str):
                    ranges.setdefault(chunk['url'], set()).add((chunk['start_offset'], chunk['start_offset'] + len(chunk['content'])))
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
    context.state['read_webpage_ranges'] = {url: sorted(windows) for url, windows in ranges.items()}

def project_tool_results(messages, raw_results, tools, context_window, output_tokens, margin):  # 只压缩模型视图，数据库与前端仍保留完整工具结果
    projected = []
    bodies = []
    seen = set()
    names = {}
    searches = []
    navigation = []
    for message in messages:
        for call in message.get("tool_calls", []):
            names[call["id"]] = call.get("function", {}).get("name")
        call_id = message.get("tool_call_id")
        if message.get("role") != "tool" or call_id not in raw_results or names.get(call_id) not in ("search_uploaded_files", "read_uploaded_file", "read_history_sources", "summarize_uploaded_files", "read_webpage", "search_web", 'delegate_task', 'get_subagent_task'):
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
        if names[call_id] in ('delegate_task', 'get_subagent_task'):
            data = {key: value for key, value in data.items() if key in (
                'task_id', 'status', 'outcome', 'task', 'summary', 'chunks', 'error', 'error_info',
                'limitations', 'source_count', 'result_truncated', 'evidence_truncated')}
            data['chunks'] = [{key: value for key, value in chunk.items() if key in (
                'citation_id', 'source_type', 'file_id', 'filename', 'url', 'title', 'start_offset', 'end_offset', 'content', 'truncated')}
                for chunk in data.get('chunks', []) if isinstance(chunk, dict)]
        if names[call_id] in ('delegate_task', 'get_subagent_task') and isinstance(data.get('summary'), str):
            bodies.append((data, 'summary', data))
        if names[call_id] == 'read_webpage' and isinstance(data.get('links'), list):
            navigation.append(data)
        if names[call_id] == 'search_web' and isinstance(data.get('results'), list):
            searches.append(data)
            for item in data['results']:
                if isinstance(item, dict) and isinstance(item.get('snippet'), str):
                    bodies.append((item, 'snippet', data))
        elif names[call_id] in ("search_uploaded_files", "read_history_sources", "summarize_uploaded_files", "read_webpage", 'delegate_task', 'get_subagent_task') and isinstance(data.get("chunks"), list):
            unique, duplicates = [], []
            for chunk in data["chunks"]:
                if not isinstance(chunk, dict) or not isinstance(chunk.get("content"), str):
                    unique.append(chunk)
                    continue
                identity = (chunk.get("file_id"), chunk.get('url'), chunk.get("chunk_id"), chunk.get("start_offset"), chunk.get("end_offset"), chunk["content"])
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
        link_heavy = [data for data in navigation if len(data['links']) > 5]
        if link_heavy:
            data = max(link_heavy, key=lambda value: len(json.dumps(value['links'], ensure_ascii=False)))
            data['links'].pop()
            data['context_notice'] = '部分入口链接因预算省略，完整列表保留在工具记录中。'
            data['context_limited'] = True
            continue
        adjustable = [body for body in bodies if len(body[0][body[1]]) > 64]
        if not adjustable:
            removable = [data for data in searches if len(data['results']) > 1]
            if removable:
                data = max(removable, key=lambda value: len(json.dumps(value, ensure_ascii=False)))
                data['results'].pop()
                data['context_limited'] = True
                data['context_notice'] = '本轮输入预算有限，已省略排名靠后的搜索候选；完整结果仍保存在工具记录中。'
                continue
            return result  # 剩余必要输入由现有预算检查明确报错，不能删除问题或工具调用
        item, field, parent = max(adjustable, key=lambda body: len(body[0][body[1]]))
        item[field] = item[field][:max(64, len(item[field]) // 2)]
        item["truncated"] = True
        parent["truncated"] = True
        parent["context_limited"] = True
        parent["context_notice"] = "部分正文因本轮输入预算被截短，不能把片段当成完整资料；必要时按偏移读取。"
        if field == 'snippet':
            parent['context_notice'] = '部分搜索摘要因输入预算被截短；摘要不是网页原文，需要依据时读取候选链接。'
        if field == "content" and isinstance(item.get("start_offset"), int):
            item["end_offset"] = item["start_offset"] + len(item[field])
            if parent.get('url') == item.get('url') and 'next_offset' in parent:
                parent['next_offset'] = item['end_offset']
                parent['has_more'] = True
            for page in parent.get('pages', []):
                if page.get('source_id') == item.get('source_id'):
                    page['next_offset'] = page.get('offset', 0) + len(item[field])
                    page['has_more'] = True
        if field == "text_content":
            item["next_offset"] = item.get("offset", item.get("start_offset", 0)) + len(item[field])
            item["has_more"] = True
