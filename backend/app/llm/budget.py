import json
import math
from typing import Any

class InputBudgetError(ValueError):
    """必要输入超过预算，或预算配置无效；说明可直接展示给用户。"""

def get_input_budget(context_window: int, output_tokens: int, margin: int) -> int:  # 为输出和消息包装预留空间
    if type(context_window) is not int or type(output_tokens) is not int or type(margin) is not int or context_window <= 0 or output_tokens <= 0 or margin < 0:
        raise InputBudgetError("上下文配置不正确：总预算和输出预留必须是正整数，安全余量不能为负数")
    budget = context_window - output_tokens - margin
    if budget <= 0:
        raise InputBudgetError("上下文配置不正确：总预算必须大于输出预留与安全余量之和")
    return budget

def estimate_text_tokens(content: str) -> int:
    """无供应商Tokenizer时的混合文本估算；中文不按三个UTF-8字节重复计数。

    ASCII约四字符一个Token，非ASCII留出更多空间；仍非精确计数或上界。
    """
    ascii_count = sum(ord(char) < 128 for char in content)
    return math.ceil(ascii_count / 4 + (len(content) - ascii_count) * 1.5)


def usage_tokens(usage) -> int:
    if not isinstance(usage, dict):
        return 0
    def number(*keys):
        return next((usage[key] for key in keys if type(usage.get(key)) is int and usage[key] > 0), 0)
    prompt = number('prompt_tokens', 'input_tokens', 'prompt_eval_count')
    return prompt + number('completion_tokens', 'output_tokens', 'eval_count') if prompt else 0


def estimate_input_tokens(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> int:
    payload = {"messages": messages}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return estimate_text_tokens(content)

def ensure_input_budget(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None, context_window: int, output_tokens: int, margin: int) -> None:  # 底层请求最终检查，超预算时禁止发送
    budget = get_input_budget(context_window, output_tokens, margin)
    if estimate_input_tokens(messages, tools) > budget:
        raise InputBudgetError("模型输入超过预算，已停止发送，请缩短问题或减少读取内容")
