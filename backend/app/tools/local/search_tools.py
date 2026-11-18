from pydantic import Field
import re
from backend.app.tools.runtime import ToolArguments, register_tool, registry


class Arguments(ToolArguments):
    query: str = Field(default='', max_length=200, description='工具名、中文用途或分类：历史会话、长期记忆、知识库、文件浏览、精确查找')
    count: int = Field(default=5, ge=1, le=8)


@register_tool(name='search_tools', label='发现可用工具', description='按用途发现并启用额外工具。支持历史会话搜索与读取、长期记忆读写、知识库发现、精确查找、按行读取和文件展示。随后请求会提供这些工具的完整参数；关闭或无权限的工具不会返回。', arguments=Arguments, uses_context=True)
async def search_tools(context, query='', count=5):
    tokens = [word.casefold() for word in query.replace('，', ' ').replace(',', ' ').split() if word]
    candidates = []
    for spec in registry._tools.values():
        if spec.name == 'search_tools' or not spec.is_available(context):
            continue
        if spec.alias_of and spec.name not in tokens:
            continue
        text = (spec.name + ' ' + spec.label + ' ' + spec.description).casefold()
        score = 0
        for token in tokens:
            if token == spec.name:
                score += 100
            elif token in text:
                score += 10
            elif re.search(r'[\u4e00-\u9fff]', token):
                score += sum(1 for index in range(len(token) - 1) if token[index:index + 2] in text)
        if not tokens or score:
            candidates.append((score, spec))
    candidates.sort(key=lambda item: (-item[0], item[1].name))
    selected = [spec for _, spec in candidates[:count]]
    # 最近发现的工具保留在有上限的活动集合内；无权限项不会因缓存重新出现。
    active = [name for name in context.state.get('discovered_tool_order', [])
              if name in registry._tools and registry._tools[name].is_available(context)]
    for spec in selected:
        if spec.name in active:
            active.remove(spec.name)
        active.append(spec.name)
    active = active[-16:]
    context.state['discovered_tool_order'] = active
    context.state['discovered_tools'] = set(active)
    return {'tools': [{'name': spec.name, 'label': spec.label, 'description': spec.description} for spec in selected],
            'active_tools': active,
            'notice': '上述工具的完整参数将在下一次模型请求中提供；本轮保留最近16个发现入口，最早项超出后可重新发现。不会授予额外文件或账号权限。'}
