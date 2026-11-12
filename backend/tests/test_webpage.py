import gzip
import json
import socket
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import httpx
from backend.app.tools.local import read_webpage as webpage
from backend.app.tools.runtime import ToolContext
from backend.app.tools.registry import execute_tool
from backend.app.chat.citations import add_citation_ids
from backend.app.chat.history_sources import collect_history_sources, read_history_sources


class ChunkStream(httpx.AsyncByteStream):
    def __init__(self, data):
        self.data = data

    async def __aiter__(self):
        for start in range(0, len(self.data), 8000):
            yield self.data[start:start + 8000]


def response(data, content_type='text/html; charset=utf-8', **headers):
    return httpx.Response(200, headers={'Content-Type': content_type, **headers}, stream=ChunkStream(data))


class WebpageTests(unittest.IsolatedAsyncioTestCase):
    async def test_navigation_only_keeps_real_links_without_citations_and_allows_followup(self):
        from backend.app.tools import browser_reader
        links = ''.join(f'<a href="/channel/{i}">频道{i}</a>' for i in range(6))
        html = f'<html><body><nav>{links}</nav><main></main></body></html>'.encode()
        client = httpx.AsyncClient
        with patch.object(webpage, 'resolve_public_ip', AsyncMock(return_value='93.184.216.34')), \
             patch.object(webpage.httpx, 'AsyncClient', lambda **kwargs: client(transport=httpx.MockTransport(lambda request: response(html)), **kwargs)), \
             patch.object(webpage, 'extract_page', return_value=('入口页', '')), \
             patch.object(browser_reader, 'render_page', AsyncMock(side_effect=ValueError('动态正文不可用'))):
            document = await webpage.fetch_page('https://example.com/')
        self.assertEqual(document['status'], 'navigation_only')
        self.assertEqual(len(document['links']), 6)
        context = ToolContext(history=[SimpleNamespace(role='user', content='读取 https://example.com/')])
        article = {'url': 'https://example.com/channel/0', 'title': '频道', 'text': '频道内实际文章', 'retrieved_at': 1}
        with patch.object(webpage, 'fetch_page', AsyncMock(side_effect=[document, article])):
            raw = await execute_tool('read_webpage', '{"url":"https://example.com/"}', context=context)
            self.assertEqual(json.loads(raw)['chunks'], [])
            self.assertNotIn('citation_id', add_citation_ids('read_webpage', raw, {}, set()))
            followed = await execute_tool('read_webpage', '{"url":"https://example.com/channel/0"}', context=context)
            self.assertEqual(json.loads(followed)['chunks'][0]['content'], article['text'])

    async def test_fetch_pins_public_ip_keeps_tls_hostname_and_extracts_html(self):
        requests = []
        article = '这是可靠的文章正文，保留数字和适用条件。' * 10
        html = f'<html><head><title>测试文章</title><script>不应返回</script></head><body><nav>导航垃圾</nav><article><h1>文章标题</h1><p>{article}</p><div hidden>隐藏垃圾</div><style>隐藏样式</style></article><footer>页脚垃圾</footer></body></html>'
        def handler(request):
            requests.append(request)
            return response(html.encode())
        client = httpx.AsyncClient
        def isolated_client(**kwargs):
            self.assertFalse(kwargs['trust_env'])
            self.assertFalse(kwargs['follow_redirects'])
            return client(transport=httpx.MockTransport(handler), **kwargs)
        dns = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))]
        with patch.object(webpage.socket, 'getaddrinfo', return_value=dns) as resolve, patch.object(webpage.httpx, 'AsyncClient', isolated_client):
            result = await webpage.fetch_page('https://example.com/article#section')
        self.assertEqual(result['title'], '测试文章')
        self.assertIn(article, result['text'])
        self.assertNotIn('垃圾', result['text'])
        self.assertNotIn('不应返回', result['text'])
        self.assertEqual(result['url'], 'https://example.com/article')
        self.assertEqual(requests[0].url.host, '93.184.216.34')
        self.assertEqual(requests[0].headers['Host'], 'example.com')
        self.assertEqual(requests[0].extensions['sni_hostname'], 'example.com')
        resolve.assert_called_once()

    async def test_url_and_mixed_dns_addresses_cannot_reach_internal_network(self):
        for url in ['file:///etc/passwd', 'ftp://example.com/a', 'https://user:password@example.com', 'http://example.com:8080/a', 'https://example.com/\nheader']:
            with self.assertRaises(ValueError):
                webpage.normalize_url(url)
        for ip in ['127.0.0.1', '10.1.2.3', '169.254.169.254', '::1', '::ffff:127.0.0.1', '64:ff9b::7f00:1', '100.64.0.1', '224.0.0.1']:
            self.assertFalse(webpage.public_address(ip), ip)
        dns = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, 443)) for ip in ['93.184.216.34', '127.0.0.1']]
        with patch.object(webpage.socket, 'getaddrinfo', return_value=dns), self.assertRaisesRegex(ValueError, '非公开'):
            await webpage.resolve_public_ip(webpage.normalize_url('https://example.com'))

    async def test_redirect_target_is_revalidated_before_connecting(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(302, headers={'Location': 'http://127.0.0.1/private'})
        client = httpx.AsyncClient
        with patch.object(webpage, 'resolve_public_ip', AsyncMock(side_effect=['93.184.216.34', ValueError('不允许内网')])) as resolve, patch.object(webpage.httpx, 'AsyncClient', lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs)):
            with self.assertRaisesRegex(ValueError, '内网'):
                await webpage.fetch_page('https://example.com')
        self.assertEqual(len(requests), 1)
        self.assertEqual(resolve.await_count, 2)

    async def test_compressed_size_and_binary_content_are_bounded(self):
        compressed = response(gzip.compress(b'x' * (webpage.MAX_BYTES + 100)), **{'Content-Encoding': 'gzip'})
        with self.assertRaisesRegex(ValueError, '过大'):
            await webpage.bounded_body(compressed)
        self.assertEqual(await webpage.bounded_body(response(gzip.compress(b'hello'), **{'Content-Encoding': 'gzip'})), b'hello')
        with self.assertRaisesRegex(ValueError, '超过1MB'):
            await webpage.bounded_body(response(b'', **{'Content-Length': str(webpage.MAX_BYTES + 1)}))
        client = httpx.AsyncClient
        with patch.object(webpage, 'resolve_public_ip', AsyncMock(return_value='93.184.216.34')), patch.object(webpage.httpx, 'AsyncClient', lambda **kwargs: client(transport=httpx.MockTransport(lambda request: response(b'%PDF', 'application/pdf')), **kwargs)):
            with self.assertRaisesRegex(ValueError, '暂不读取PDF'):
                await webpage.fetch_page('https://example.com')

    async def test_pagination_uses_same_answer_snapshot_and_history_rebases_citations(self):
        document = {'url': 'https://example.com/article', 'title': '文章', 'text': '第一段😀第二段', 'retrieved_at': 100}
        context = ToolContext()
        with patch.object(webpage, 'fetch_page', AsyncMock(return_value=document)) as fetch:
            first = await execute_tool('read_webpage', '{"url":"https://example.com/article","max_chars":4}', context=context)
            second = json.loads(await execute_tool('read_webpage', '{"url":"https://example.com/article","offset":4}', context=context))
        fetch.assert_awaited_once()
        self.assertEqual(second['chunks'][0]['content'], '第二段')
        numbered = add_citation_ids('read_webpage', first, {}, set())
        data = json.loads(numbered)
        self.assertEqual(data['chunks'][0]['citation_id'], 1)
        self.assertTrue(data['has_more'])
        output = [{'type': 'function_call', 'call_id': 'web', 'name': 'read_webpage'}, {'type': 'function_call_output', 'call_id': 'web', 'output': numbered}]
        history = collect_history_sources([SimpleNamespace(role='assistant', status='completed', output=output, id='answer', created_at=120)])
        snapshot = read_history_sources(['H1'], history)
        rebased = json.loads(add_citation_ids('read_history_sources', json.dumps(snapshot), {('old',): 1}, set()))
        self.assertEqual(rebased['chunks'][0]['citation_id'], 2)
        self.assertEqual(rebased['chunks'][0]['url'], document['url'])
        self.assertEqual(rebased['chunks'][0]['retrieved_at'], 100)
        self.assertEqual(rebased['chunks'][0]['origin_message_id'], 'answer')

    async def test_charset_plain_text_and_unsafe_web_citation(self):
        html = '<meta charset="gbk"><p>中文网页正文</p>'.encode('gbk')
        self.assertIn('中文网页正文', webpage.extract_page(html, 'text/html', None)[1])
        self.assertEqual(webpage.extract_page(b'first\nsecond', 'text/plain', None)[1], 'first\nsecond')
        with self.assertRaisesRegex(ValueError, '登录'):
            webpage.extract_page(b'<h1>Sign in</h1><form><input type="password"></form>', 'text/html', None)
        with self.assertRaisesRegex(ValueError, 'JavaScript'):
            webpage.extract_page(b'<p>Please enable JavaScript</p>', 'text/html', None)
        bad = {'chunks': [{'source_type': 'web', 'url': 'javascript:alert(1)', 'title': '错误链接', 'content': '内容', 'start_offset': 0}]}
        self.assertNotIn('citation_id', add_citation_ids('read_webpage', json.dumps(bad), {}, set()))

    async def test_chat_cannot_construct_new_urls_or_query_parameters(self):
        context = ToolContext(history=[SimpleNamespace(role='user', content='总结 [文章](https://example.com/article)。')])
        document = {'url': 'https://example.com/final', 'title': '文章', 'text': '正文', 'retrieved_at': 100}
        with patch.object(webpage, 'fetch_page', AsyncMock(return_value=document)) as fetch:
            with self.assertRaisesRegex(ValueError, '不能自行拼接'):
                await webpage.read_webpage(context, 'https://example.com/article?data=secret')
            await webpage.read_webpage(context, 'https://example.com/article')
            await webpage.read_webpage(context, 'https://example.com/final')
        fetch.assert_awaited_once()
