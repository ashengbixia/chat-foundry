import json
import re
from backend.app.chat.source_types import is_web_source, FILE_BROWSING_SOURCE_TOOLS, ACCOUNT_FILE_SOURCE_TOOLS


def executed_citation_numbers(output):
    """Only newly executed source tools justify an automatic 'read sources' footer."""
    from backend.app.chat.history_sources import SOURCE_TOOLS
    calls, numbers = {}, set()
    for item in output:
        if item.get('type') == 'function_call':
            calls[item.get('call_id')] = item.get('name')
        elif item.get('type') == 'function_call_output' and calls.get(item.get('call_id')) in SOURCE_TOOLS:
            try:
                data = json.loads(item['output'])
                chunks = [data] if calls[item['call_id']] == 'read_uploaded_file' else data.get('chunks', [])
                numbers.update(chunk['citation_id'] for chunk in chunks if isinstance(chunk, dict) and type(chunk.get('citation_id')) is int)
            except (ValueError, TypeError, KeyError, AttributeError):
                continue
    return numbers


def retain_cited_context_sources(output, text):
    """Keep snapshots cited by this answer; do not copy every old document on small talk."""
    parts = re.split(r'(```[\s\S]*?```|`[^`\n]+`)', text)
    cited = {int(value) for part in parts[::2] for match in re.findall(r'\[(\d+)\]|[〔【\[](?:引用\s*)(\d+)[〕】\]]', part) for value in match if value}
    for item in output:
        if item.get('type') == 'context_sources':
            item['chunks'] = [chunk for chunk in item.get('chunks', []) if chunk.get('citation_id') in cited]


def missing_source_footer(text, numbers):
    """只列出实际读取的来源，不把未标注的每句话自动判为有依据。"""
    numbers = sorted(set(numbers))
    if not text.strip() or not numbers:
        return ''
    parts = re.split(r'(```[\s\S]*?```|`[^`\n]+`)', text)
    cited = {int(number) for part in parts[::2] for number in re.findall(r'\[(\d+)\]', part)}
    cited.update(int(number) for part in parts[::2] for number in re.findall(r'[〔【\[](?:citation_id\s*[:：]\s*|引用\s*)(\d+)[〕】\]]', part, flags=re.I))
    if cited.intersection(numbers):
        return ''
    return '\n\n本轮已读取来源：' + ' '.join(f'[{number}]' for number in numbers)

def add_citation_ids(name: str, result: str, sources: dict, allowed_file_ids: set[str]) -> str:  # 只给本轮实际返回、且在允许范围内的附件正文编号
    if name not in {"search_uploaded_files", "read_uploaded_file", "read_history_sources", "summarize_uploaded_files", "read_webpage", 'delegate_task', 'get_subagent_task'} | FILE_BROWSING_SOURCE_TOOLS:
        return result
    try:
        data = json.loads(result)
    except (ValueError, TypeError):
        return result
    if not isinstance(data, dict) or "error" in data:
        return result
    items = [data] if name == "read_uploaded_file" else data.get("chunks", [])
    if not isinstance(items, list):
        return result
    changed = False
    for item in items:
        if not isinstance(item, dict):
            continue
        web = is_web_source(item) and name in ('read_webpage', 'read_history_sources', 'delegate_task', 'get_subagent_task')
        if not web and (not isinstance(item.get("file_id"), str) or (name not in {'read_history_sources', 'delegate_task', 'get_subagent_task'} | ACCOUNT_FILE_SOURCE_TOOLS and item["file_id"] not in allowed_file_ids)):
            continue
        content = item.get("text_content" if name == "read_uploaded_file" else "content")
        start = item.get("offset" if name == "read_uploaded_file" else "start_offset")
        if not isinstance(content, str) or not content.strip() or type(start) is not int or start < 0 or (not web and not isinstance(item.get("filename"), str)):
            continue
        identity = ('web', item['url'], start, content) if web else (item["file_id"], start, content)
        base = ('web', item['url']) if web else (item['file_id'],)
        containing = next((number for key, number in sources.items()
                           if key[:-2] == base and key[-2] <= start
                           and key[-1][start - key[-2]:start - key[-2] + len(content)] == content), None)
        if containing is not None:
            item['citation_id'] = containing
            changed = True
            continue
        if identity not in sources:
            sources[identity] = len(sources) + 1
        item["citation_id"] = sources[identity]
        changed = True
    if name in ('delegate_task', 'get_subagent_task') and isinstance(data.get('summary'), str):
        mapping = {str(item['child_citation_id']): item['citation_id'] for item in items if isinstance(item, dict) and 'child_citation_id' in item and 'citation_id' in item}
        parts = re.split(r'(```[\s\S]*?```|`[^`\n]+`)', data['summary'])
        for index in range(0, len(parts), 2):
            def remap(match):
                number = next(group for group in match.groups() if group is not None)
                return '[' + str(mapping[number]) + ']' if number in mapping else '（子任务原编号' + number + '）'
            parts[index] = re.sub(r'\[(\d+)\]|[〔【\[](?:citation_id\s*[:：]\s*|引用\s*)(\d+)[〕】\]]', remap, parts[index], flags=re.IGNORECASE)
        data['summary'] = ''.join(parts)
        changed = True
    return json.dumps(data, ensure_ascii=False) if changed else result
