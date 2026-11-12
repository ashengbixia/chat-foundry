"""研究结果与失败收尾。只依据真实记录，不额外调用模型。"""
import json
import re
import httpx
from sqlalchemy.exc import SQLAlchemyError
from backend.app.llm.budget import InputBudgetError
from backend.app.llm.client import ModelStreamError


def assess_result(message):
    if message is None:
        return {'outcome': 'failed', 'source_count': 0, 'limitations': '子运行未正常完成'}
    calls, sources, researched = {}, set(), False
    for item in message.output or []:
        if not isinstance(item, dict):
            continue
        if item.get('type') == 'function_call':
            calls[item.get('call_id')] = item.get('name')
        elif item.get('type') == 'function_call_output':
            name = calls.get(item.get('call_id'))
            researched |= name in ('search_web', 'read_webpage', 'search_uploaded_files', 'read_uploaded_file', 'read_history_sources', 'summarize_uploaded_files')
            try:
                data = json.loads(item['output'])
                chunks = [data] if name == 'read_uploaded_file' else data.get('chunks', [])
                sources.update(chunk['citation_id'] for chunk in chunks if isinstance(chunk, dict) and type(chunk.get('citation_id')) is int)
            except (ValueError, TypeError, KeyError, AttributeError):
                pass
    if message.status != 'completed':
        return {'outcome': 'failed', 'source_count': len(sources), 'limitations': '子运行未正常完成，已有资料仍保留在子历史中'}
    negative = re.search(r'[^\n。]*(?:未能找到|未找到|无法核实|没有找到|未取得足够|没有可核实|无法按要求[^\n。]*(?:整理|核实))[^\n。]*', message.content[:700])
    insufficient = researched and (not sources or negative is not None)
    return {'outcome': 'insufficient_evidence' if insufficient else 'answered', 'source_count': len(sources),
            'limitations': negative.group(0).strip('*# ')[:240] if negative else '尚未取得可引用的原文依据' if insufficient else ''}


def failure_info(error, phase):
    info = {'phase': phase, 'code': 'execution_error', 'retryable': False, 'type': type(error).__name__}
    if isinstance(error, InputBudgetError):
        info.update(phase='input_budget', code='input_budget_exceeded')
        message = '本轮输入预算不足（按保守估算），已保留生成记录'
    elif isinstance(error, SQLAlchemyError):
        info.update(phase='database', code='database_error')
        message = '数据库读写未完成，请查看后端日志'
    elif isinstance(error, httpx.TimeoutException):
        info.update(phase='model_request', code='model_timeout', retryable=True)
        message = '模型请求超时，已保留取得的结果'
    elif isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        info.update(phase='model_request', code=f'model_http_{status}', retryable=status == 429 or status >= 500)
        message = f'模型服务返回HTTP {status}，本轮未完整完成'
    elif isinstance(error, ModelStreamError):
        info.update(phase='model_response', code='model_stream_incomplete', retryable=True)
        message = str(error)
    else:
        message = '生成或保存回答失败，请查看后端日志'
    return info, message


def result_data(raw_results):
    for raw in raw_results.values():
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                yield data
        except (ValueError, TypeError):
            continue


def exhausted_research(raw_results):
    tasks = {data['task_id']: data for data in result_data(raw_results) if data.get('task_id')}
    return bool(tasks) and all(task.get('status') not in ('queued', 'running', 'cancelling') and
        (task.get('outcome') in ('failed', 'insufficient_evidence') or task.get('status') != 'completed') for task in tasks.values())


def failure_report(raw_results, error_info):
    data = list(result_data(raw_results))
    if not data:
        return ''
    labels = {'answered': '已返回结论，尚未完成主会话汇总', 'insufficient_evidence': '执行结束，但未取得足够证据', 'failed': '执行失败'}
    lines = ['本轮研究未能完整完成，已保留取得的结果：', '']
    tasks = list({item['task_id']: item for item in data if item.get('task_id')}.values())
    for index, item in enumerate(tasks[:4], 1):
        status = labels.get(item.get('outcome'), '仍在运行' if item.get('status') in ('queued', 'running') else '未完整完成')
        lines.append(f"- 子任务 {index}：{status}。")
        if item.get('summary'):
            lines.append('  已返回的子任务摘要：' + item['summary'][:600])
        if item.get('limitations'):
            lines.append('  ' + item['limitations'][:220])
    if not tasks:
        candidates = sum(len(item.get('results', [])) for item in data)
        sources = sum(len(item.get('chunks', [])) for item in data)
        lines.append(f'已取得 {sources} 段原文及 {candidates} 条搜索候选；候选摘要尚不等于已核实的结论。')
    reason = '输入预算不足' if error_info['code'] == 'input_budget_exceeded' else '模型请求或执行未正常结束'
    lines.extend(['', f'由于{reason}，没有继续补搜或补造结论。完整过程与已返回来源保留在工具记录中。'])
    return '\n'.join(lines)
