"""使用小请求验证聊天、流式、工具及回传；不执行实际业务工具，不保存草稿。"""
import asyncio
from contextlib import aclosing
import json
import time
import httpx
from fastapi import HTTPException
from backend.app.llm.client import ModelStreamError, stream_model_events
from backend.app.llm.http import ModelAccessError
from backend.app.llm.messages import assistant_tool_message


async def collect(messages, connection, tools=None, tool_choice=None):
    text, reasoning, calls = [], [], {}
    async with aclosing(stream_model_events(messages, tools, connection, max_output_tokens=1024, tool_choice=tool_choice)) as events:
        async for event in events:
            if event['type'] == 'text_delta':
                text.append(event['text'])
            elif event['type'] == 'reasoning_delta':
                reasoning.append(event['text'])
            elif event['type'] == 'tool_call_delta':
                for fragment in event['tool_calls']:
                    call = calls.setdefault(fragment['index'], {'id': '', 'type': 'function', 'function': {'name': '', 'arguments': ''}})
                    call['id'] += fragment.get('id') or ''
                    for key in ('name', 'arguments'):
                        call['function'][key] += (fragment.get('function') or {}).get(key) or ''
    return ''.join(text), ''.join(reasoning), list(calls.values())


def diagnostic_error(error):
    if isinstance(error, ModelAccessError):
        return 'address_denied', str(error), 'failed'
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        labels = {400: '模型拒绝请求参数，请检查工具、思考模式和消息兼容设置', 401: 'API Key认证失败',
                  403: '服务拒绝访问，请检查权限', 404: '模型或接口不存在', 429: '请求限流或额度不足'}
        return f'http_{status}', labels.get(status, '模型服务未完成请求') + f'（HTTP {status}）', 'failed'
    if isinstance(error, httpx.TimeoutException) or isinstance(error, asyncio.TimeoutError):
        return 'timeout', '请求或排队超时，不能据此判断模型不支持此能力', 'failed'
    if isinstance(error, httpx.RequestError):
        return 'network_error', '无法连接模型服务，请检查网络和证书', 'failed'
    if isinstance(error, HTTPException):
        return f'request_{error.status_code}', str(error.detail), 'failed'
    if isinstance(error, ModelStreamError):
        limited = '长度限制' in str(error)
        return 'output_limit' if limited else 'response_error', str(error), 'limited' if limited else 'failed'
    return 'invalid_response', '模型响应格式不兼容或未完成本项验证', 'failed'


async def diagnose_model(connection, plain_request):
    checks = []

    async def run(check_id, label, operation):
        started = time.monotonic()
        try:
            result = await asyncio.wait_for(operation(), timeout=min(connection.timeout, 60) + 2)
            checks.append({'id': check_id, 'label': label, 'status': 'passed', 'message': '已验证', 'duration_ms': round((time.monotonic() - started) * 1000)})
            return result
        except Exception as error:
            code, message, status = diagnostic_error(error)
            checks.append({'id': check_id, 'label': label, 'status': status, 'code': code, 'message': message, 'duration_ms': round((time.monotonic() - started) * 1000)})
            return None

    def skip(check_id, label, message):
        checks.append({'id': check_id, 'label': label, 'status': 'skipped', 'message': message, 'duration_ms': 0})

    async def plain():
        response = await plain_request()
        choice = response['choices'][0]
        if choice.get('finish_reason') == 'length':
            raise ModelStreamError('模型输出达到长度限制，本次回答不完整')
        if not isinstance(choice['message'].get('content'), str) or not choice['message']['content'].strip():
            if choice.get('finish_reason') == 'length':
                raise ModelStreamError('模型输出达到长度限制，本次回答不完整')
            raise ModelStreamError('模型没有返回正文或工具调用')
        return choice['message']

    async def answer(messages):
        result = await collect(messages, connection)
        if result[2] or not result[0].strip():
            raise ModelStreamError('模型未完成预期的文本回答，尚未验证本项能力')
        return result

    await run('chat', '普通聊天', plain)
    if checks[0]['status'] == 'failed':
        for key, label in [('stream', '流式输出'), ('tools', '工具调用'), ('tool_followup', '工具结果回传')]:
            skip(key, label, '基础请求失败，修复后再验证')
    else:
        if connection.capabilities.streaming:
            await run('stream', '流式输出', lambda: answer([{'role': 'user', 'content': 'Reply only OK.'}]))
        else:
            skip('stream', '流式输出', '当前配置已关闭流式输出')
        if not connection.capabilities.tools:
            skip('tools', '工具调用', '当前配置已关闭工具调用')
            skip('tool_followup', '工具结果回传', '当前配置已关闭工具调用')
        else:
            messages = [{'role': 'user', 'content': 'Call diagnostic_echo with value "ok", then answer from its result.'}]
            tools = [{'type': 'function', 'function': {'name': 'diagnostic_echo', 'description': 'Return the supplied diagnostic value; no external action.',
                       'parameters': {'type': 'object', 'properties': {'value': {'type': 'string'}}, 'required': ['value'], 'additionalProperties': False}}}]

            async def tool_request():
                result = await collect(messages, connection, tools, 'auto')
                if not result[2] or any(not call['id'] or call['function']['name'] != 'diagnostic_echo' for call in result[2]):
                    raise ModelStreamError('模型没有返回预期的诊断工具调用')
                for call in result[2]:
                    value = json.loads(call['function']['arguments'])
                    if not isinstance(value, dict) or not isinstance(value.get('value'), str) or len(value['value']) > 200:
                        raise ModelStreamError('诊断工具参数格式不兼容')
                return result

            result = await run('tools', '工具调用', tool_request)
            if result is None:
                skip('tool_followup', '工具结果回传', '未取得有效工具调用，无法继续回传')
            else:
                text, reasoning, calls = result
                messages.append(assistant_tool_message(calls, text or None, reasoning))
                for call in calls:
                    messages.append({'role': 'tool', 'tool_call_id': call['id'], 'content': call['function']['arguments']})
                await run('tool_followup', '工具结果回传', lambda: answer(messages))
    failed = any(check['status'] == 'failed' for check in checks)
    partial = any(check['status'] == 'limited' for check in checks)
    return {'status': 'failed' if failed else 'partial' if partial else 'ok', 'checks': checks,
            'message': '部分检查未通过，请查看对应项目' if failed or partial else '已通过当前启用能力的检查；已关闭的项目未测试'}
