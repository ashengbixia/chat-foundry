"""拦截误放进正文的工具协议；不把文本转换成可执行调用。"""


class UnexpectedToolMarkup(ValueError):
    pass


class ToolMarkupGuard:
    """只检查代码块/行内代码以外的DSML标记，保留跨SSE分片的前缀。"""
    prefixes = ('<｜｜DSML｜｜', '</｜｜DSML｜｜', '<|DSML|', '</|DSML|')

    def __init__(self):
        self.pending = ''
        self.fenced = False
        self.inline = False

    def feed(self, text, *, final=False):
        self.pending += text
        if '<' not in self.pending and '`' not in self.pending:
            result, self.pending = self.pending, ''
            if '\n' in result and not self.fenced:
                self.inline = False
            return result
        result = []
        pending, position = self.pending, 0
        while position < len(pending):
            if pending[position] == '`':
                if not final and len(pending) - position <= 2 and pending[position:] in ('`', '``'):
                    break
                if pending.startswith('```', position):
                    self.fenced = not self.fenced
                    self.inline = False
                    result.append('```')
                    position += 3
                    continue
                if not self.fenced:
                    self.inline = not self.inline
            if not self.fenced and not self.inline:
                if any(pending.startswith(prefix, position) for prefix in self.prefixes):
                    raise UnexpectedToolMarkup('模型把工具协议标记放进了正文，未执行其中的调用。请重试或检查模型服务的工具调用兼容性。')
                tail = pending[position:] if len(pending) - position < 12 else None
                if not final and tail is not None and any(prefix.startswith(tail) for prefix in self.prefixes):
                    break
                if final and tail is not None and 'DSML' in tail and any(prefix.startswith(tail) for prefix in self.prefixes):
                    raise UnexpectedToolMarkup('模型返回了不完整的工具协议标记，未执行其中的调用。')
            char = pending[position]
            position += 1
            result.append(char)
            if char == '\n' and not self.fenced:
                self.inline = False
        self.pending = pending[position:]
        return ''.join(result)


def safe_history_text(text):
    """历史展示与存储不动，仅替换发送给模型的异常助手正文。"""
    if not isinstance(text, str):
        return text
    try:
        return ToolMarkupGuard().feed(text, final=True)
    except UnexpectedToolMarkup:
        return '（历史助手回复包含未执行的异常工具协议，已从模型输入移除；不能视为工具已执行或事实已核实。）'
