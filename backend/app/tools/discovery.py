"""额外工具按需提供参数，避免工具清单占满上下文。权限仍由is_available校验。"""


def discovered(name):
    return lambda ctx: name in ctx.state.get('discovered_tools', set())


def resource_entry(name, category='knowledge'):
    """目录存在时直接提供常用入口；没有目录时仍可按需发现。"""
    key = 'knowledge_catalog_available' if category == 'knowledge' else 'account_files_available'
    return lambda ctx: bool(ctx.state.get(key)) or discovered(name)(ctx)


def knowledge_catalog_needs_page(ctx):
    data = ctx.state.get('resource_catalog', {}).get('knowledge', {})
    return bool(data.get('has_more')) or any(item.get('description_truncated') for item in data.get('knowledge_bases', [])) or discovered('list_knowledge_bases')(ctx)
