"""验证并合并用户答案，与新用户消息在同一事务保存。"""
import copy
import json


def pending_question(output):
    calls = {item.get('call_id') for item in output if isinstance(item, dict) and item.get('type') == 'function_call' and item.get('name') == 'ask_user'}
    for item in output:
        if isinstance(item, dict) and item.get('type') == 'function_call_output' and item.get('call_id') in calls:
            try:
                data = json.loads(item['output'])
                if isinstance(data, dict) and data.get('status') == 'awaiting_user':
                    return item['call_id'], data
            except (ValueError, KeyError, TypeError):
                continue
    return None


def resolve_question(message, text, reply=None):
    pending = pending_question(message.output or []) if message and message.role == 'assistant' and message.status == 'completed' else None
    if reply is not None and (not pending or reply.message_id != message.id or reply.call_id != pending[0]):
        raise ValueError('这条追问已处理或会话已继续，请刷新后查看最新消息')
    if not pending:
        return
    call_id, data = pending
    if reply is not None:
        if not reply.cancelled:
            questions = {item['id']: item for item in data['questions']}
            if set(reply.answers) != set(questions):
                raise ValueError('请回答全部问题，或选择跳过')
            for key, answer in reply.answers.items():
                if not answer.strip() or (not questions[key].get('allow_other', True) and answer not in {item['label'] for item in questions[key]['options']}):
                    raise ValueError('答案为空或不属于允许的选项')
        data.update(status='cancelled' if reply.cancelled else 'answered', answers={} if reply.cancelled else reply.answers)
    else:
        data.update(status='answered', answers={'free_text': text})
    data['notice'] = '用户已跳过本次追问，不得代选；依据已知信息继续并说明缺口。' if data['status'] == 'cancelled' else '这是用户已提交的真实答案，请结合原任务继续。'
    output = copy.deepcopy(message.output)
    for item in output:
        if item.get('type') == 'function_call_output' and item.get('call_id') == call_id:
            item['output'] = json.dumps(data, ensure_ascii=False)
    message.output = output


def was_skipped(message):
    if message is None or message.role != 'assistant':
        return False
    output = getattr(message, 'output', None) or []
    ids = {item.get('call_id') for item in output if isinstance(item, dict) and item.get('type') == 'function_call' and item.get('name') == 'ask_user'}
    for item in output:
        if isinstance(item, dict) and item.get('type') == 'function_call_output' and item.get('call_id') in ids:
            try:
                if json.loads(item['output']).get('status') == 'cancelled':
                    return True
            except (ValueError, TypeError, KeyError, AttributeError):
                continue
    return False
