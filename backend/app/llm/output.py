"""输出额度与输入预算预留分开：未配置时不替供应商指定最大输出。"""


def request_limits(settings, connection, override=None):
    capabilities = getattr(connection, 'capabilities', None)
    limit = getattr(capabilities, 'max_output_tokens', None)
    if override is not None:
        limit = min(override, limit) if limit is not None else override
    window = getattr(capabilities, 'context_window', None) or settings.llm_context_window
    # 服务默认输出上限未知，保留应用已有预留，但不把预留值作为max_tokens发送。
    reserve = limit if limit is not None else settings.llm_max_output_tokens
    return window, reserve, limit


def apply_output_limit(payload, connection, limit):
    result = dict(payload)
    result.pop('max_tokens', None)
    result.pop('max_completion_tokens', None)
    if limit is not None:
        key = getattr(getattr(connection, 'capabilities', None), 'output_token_parameter', 'max_tokens')
        result[key] = limit
    return result
