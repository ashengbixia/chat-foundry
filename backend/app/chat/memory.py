"""请求内恢复证据；数据库中的原回答、原编号和原工具记录保持不变。"""
import copy
import hashlib
import json
import re
from types import SimpleNamespace
from backend.app.chat.citations import add_citation_ids
from backend.app.chat.history_sources import collect_history_sources, SOURCE_TOOLS


def replay_history(history, citation_sources):
    restored, snapshots, raw_results = [], {}, {}
    for message in history:
        if message.role != 'assistant':
            restored.append(message)
            continue
        items = [copy.deepcopy(item) for item in (getattr(message, 'output', []) or []) if isinstance(item, dict)]
        for item in items:
            if item.get('type') == 'context_sources':
                item['chunks'] = [chunk for chunk in item.get('chunks', []) if chunk.get('in_context') is not False]
        source_message = SimpleNamespace(role=message.role, status=message.status, output=items,
                                         id=getattr(message, 'id', None), created_at=getattr(message, 'created_at', None))
        sources = list(collect_history_sources([source_message], limit=None).values())
        # Use per-message identities: old answers reuse citation numbers across turns.
        numbered = json.loads(add_citation_ids('read_history_sources', json.dumps({'chunks': sources}, ensure_ascii=False), citation_sources, set()))['chunks']
        identity = lambda x: (x.get('file_id', x.get('url')), x['start_offset'], x['content'])
        numbers = {identity(item): item['citation_id'] for item in numbered}
        mapping, calls = {}, {}
        cached = []
        for item in items:
            if item.get('type') == 'context_sources':
                for chunk in item.get('chunks', []):
                    key = (chunk.get('file_id', chunk.get('url')), chunk.get('start_offset'), chunk.get('content'))
                    if key in numbers:
                        mapping[str(chunk.get('citation_id'))] = numbers[key]
                        if key not in snapshots:
                            cached.append(dict(chunk, historical=True))
        items = [item for item in items if item.get('type') != 'context_sources']
        if cached:
            cached_id = 'cached_' + hashlib.sha256(str(getattr(message, 'id', '')).encode()).hexdigest()[:24]
            items = [{'type': 'function_call', 'call_id': cached_id, 'name': 'read_history_sources', 'arguments': '{}', 'reasoning_content': ''},
                     {'type': 'function_call_output', 'call_id': cached_id, 'output': json.dumps({'chunks': cached}, ensure_ascii=False)}] + items
        for item in items:
            if item.get('type') == 'function_call' and isinstance(item.get('call_id'), str):
                old = item['call_id']
                item['call_id'] = 'hist_' + hashlib.sha256(f'{getattr(message, "id", "")}/{old}'.encode()).hexdigest()[:24]
                calls[old] = (item['call_id'], item.get('name'))
            elif item.get('type') == 'function_call_output' and item.get('call_id') in calls and isinstance(item.get('output'), str):
                call_id, name = calls[item['call_id']]
                item['call_id'] = call_id
                if name in SOURCE_TOOLS:
                    try:
                        data = json.loads(item['output'])
                        chunks = [data] if name == 'read_uploaded_file' else data.get('chunks', [])
                        for chunk in chunks:
                            if not isinstance(chunk, dict):
                                continue
                            key = (chunk.get('file_id', chunk.get('url')), chunk.get('offset' if name == 'read_uploaded_file' else 'start_offset'), chunk.get('text_content' if name == 'read_uploaded_file' else 'content'))
                            if key in numbers:
                                mapping[str(chunk.get('citation_id'))] = numbers[key]
                                chunk.update(citation_id=numbers[key], historical=True, origin_message_id=chunk.get('origin_message_id') or getattr(message, 'id', None))
                        item['output'] = json.dumps(data, ensure_ascii=False)
                    except (ValueError, TypeError, AttributeError):
                        pass
                raw_results[call_id] = item['output']
        def rewrite(text):
            def replace(match):
                old = next(value for value in match.groups() if value is not None)
                return '[' + str(mapping[old]) + ']' if old in mapping else '（来源见原回答）'
            parts = re.split(r'(```[\s\S]*?```|`[^`\n]+`)', text)
            for index in range(0, len(parts), 2):
                parts[index] = re.sub(r'\[(\d+)\]|[〔【\[](?:引用\s*)(\d+)[〕】\]]', replace, parts[index])
            return ''.join(parts)
        for item in items:
            if item.get('type') == 'message':
                for part in item.get('content', []):
                    if isinstance(part, dict) and isinstance(part.get('text'), str):
                        part['text'] = rewrite(part['text'])
            elif item.get('type') == 'function_call_output':
                try:
                    data = json.loads(item['output'])
                    if isinstance(data, dict) and isinstance(data.get('summary'), str):
                        data['summary'] = rewrite(data['summary'])
                        item['output'] = json.dumps(data, ensure_ascii=False)
                        raw_results[item['call_id']] = item['output']
                except (ValueError, TypeError, KeyError):
                    pass
        items = [item for item in items if not (item.get('type') == 'message' and item.get('phase') == 'commentary')]
        restored.append(SimpleNamespace(role=message.role, status=message.status, content=rewrite(message.content), output=items))
        snapshots.update((identity(item), item) for item in numbered)
    return restored, list(snapshots.values()), raw_results
