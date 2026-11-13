"""从用户明确给出的站点和真实链接确定浏览范围，不预设网站或文档入口。"""
import ipaddress
import re
from urllib.parse import urlsplit


def origins_from_urls(urls):
    origins = set()
    for value in urls:
        try:
            url = urlsplit(value)
            if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.port not in (None, 80 if url.scheme == 'http' else 443):
                continue
            try:
                ipaddress.ip_address(url.hostname)
            except ValueError:
                origins.add(url.scheme + '://' + url.hostname.lower())
        except (ValueError, TypeError):
            continue
    return frozenset(origins)


def requested_origins(history):
    text = next((item.content for item in reversed(history or []) if item.role == 'user'), '')
    origins = set()
    # 用户明确写出的公开站点也可浏览同站路径；不接受IP、凭据、端口或模型提供的域名。
    for scheme, host, port in re.findall(r'(?<![\w@.-])(?:(https?)://)?((?:[a-z0-9-]+\.)+[a-z]{2,})(?::(\d+))?(?=[/\s,，。;；)]|$)', text, re.I):
        origins.add((scheme.lower() or 'https') + '://' + host.lower() + (':' + port if port else ''))
    return origins_from_urls(origins)


def within_requested_site(context, url, known_urls=()):
    parsed = urlsplit(str(url))
    # 参数只能来自用户原链接或真实页面链接，不能借站点浏览权限追加数据。
    return not parsed.query and not parsed.username and not parsed.password and parsed.netloc == parsed.hostname and (
        parsed.scheme + '://' + parsed.netloc) in (context.web_origins | origins_from_urls(known_urls))
