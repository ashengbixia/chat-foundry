from urllib.parse import urlsplit


def is_web_source(item):
    if not isinstance(item, dict) or item.get('source_type') != 'web' or not isinstance(item.get('url'), str) or not isinstance(item.get('title'), str):
        return False
    try:
        url = urlsplit(item['url'])
        return bool(url.scheme in ('http', 'https') and url.hostname and not url.username and not url.password and url.port in (None, 80, 443) and not any(ord(char) <= 32 or ord(char) == 127 for char in item['url']))
    except ValueError:
        return False
