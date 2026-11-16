"""额外工具按需提供参数，避免工具清单占满上下文。权限仍由is_available校验。"""


def discovered(name):
    return lambda ctx: name in ctx.state.get('discovered_tools', set())
