import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import httpx
from pydantic import SecretStr
from backend.app.tools.local import search_web as web_search, read_webpage as webpage
from backend.app.tools.runtime import ToolContext
from backend.app.tools.registry import execute_tool, get_available_tools
from backend.app.chat.citations import add_citation_ids
from backend.app.chat.history_sources import collect_history_sources
from backend.tests.test_webpage import ChunkStream


class WebSearchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = SimpleNamespace(web_search_enabled=True, web_search_provider='bing', web_search_api_key=SecretStr(''))
        self.change = patch.object(web_search, 'settings', self.config)
        self.change.start()
        self.addCleanup(self.change.stop)

    async def test_free_providers_parse_filter_ads_and_deduplicate_urls(self):
        self.config.web_search_provider = 'duckduckgo'
        html = b'<div class="result"><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fdoc">Official <b>Docs</b></a><a class="result__snippet">A useful snippet</a></div><div class="result result--ad"><a class="result__a" href="https://ads.example.com">Ad</a></div><div class="result"><a class="result__a" href="https://example.com/doc">Duplicate</a></div><div class="result"><a class="result__a" href="http://127.0.0.1/private">Private</a></div>'
        with patch.object(web_search, 'request_search', AsyncMock(return_value=html)):
            result = await web_search.search_provider('Python', 5)
        self.assertEqual(result, [{'title': 'Official Docs', 'url': 'https://example.com/doc', 'snippet': 'A useful snippet'}])
        self.config.web_search_provider = 'bing'
        rss = b'<rss><channel><item><title>Docs</title><link>https://example.com/doc</link><description>Useful</description></item></channel></rss>'
        with patch.object(web_search, 'request_search', AsyncMock(return_value=rss)):
            self.assertEqual((await web_search.search_provider('Python', 1))[0]['title'], 'Docs')
        with patch.object(web_search, 'request_search', AsyncMock(return_value=b'<!DOCTYPE rss><rss/>')):
            with self.assertRaisesRegex(ValueError, 'RSS'):
                await web_search.search_provider('Python', 1)

    async def test_paid_providers_use_separate_key_and_only_return_snippets(self):
        self.config.web_search_provider = 'tavily'
        with patch.object(web_search, 'request_search', AsyncMock()) as request:
            with self.assertRaisesRegex(ValueError, '独立的WEB_SEARCH_API_KEY'):
                await web_search.search_provider('query', 2)
            request.assert_not_awaited()
        self.config.web_search_api_key = SecretStr('fake-search-only-key')
        body = json.dumps({'results': [{'title': 'Docs', 'url': 'https://example.com', 'content': 'Snippet', 'raw_content': 'not returned'}]}).encode()
        with patch.object(web_search, 'request_search', AsyncMock(return_value=body)) as request:
            result = await web_search.search_provider('query', 2)
        self.assertEqual(request.await_args.args[2]['Authorization'], 'Bearer fake-search-only-key')
        self.assertFalse(request.await_args.args[3]['include_raw_content'])
        self.assertNotIn('fake-search-only-key', json.dumps(result))
        self.assertNotIn('raw_content', result[0])
        self.config.web_search_provider = 'brave'
        with patch.object(web_search, 'request_search', AsyncMock(return_value=b'{"web":{"results":[{"title":"Docs","url":"https://example.com","description":"Snippet"}]}}')) as request:
            self.assertEqual((await web_search.search_provider('query', 2))[0]['snippet'], 'Snippet')
        self.assertEqual(request.await_args.kwargs['headers']['X-Subscription-Token'], 'fake-search-only-key')

    async def test_credentials_are_not_forwarded_to_api_redirects(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(302, headers={'Location': 'https://evil.example/collect'})
        client = httpx.AsyncClient
        with patch.object(web_search, 'resolve_public_ip', AsyncMock(return_value='93.184.216.34')), patch.object(web_search.httpx, 'AsyncClient', lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs)):
            with self.assertRaisesRegex(ValueError, 'HTTP 302'):
                await web_search.request_search('https://api.tavily.com/search', 'POST', {'Authorization': 'Bearer fake-key'}, {'query': 'query'})
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].headers['Host'], 'api.tavily.com')
        self.assertEqual(requests[0].url.host, '93.184.216.34')
        self.assertEqual(requests[0].extensions['sni_hostname'], 'api.tavily.com')

    async def test_free_redirects_stay_within_vendor(self):
        requests = []
        def handler(request):
            requests.append(request)
            if len(requests) == 1:
                return httpx.Response(302, headers={'Location': 'https://cn.bing.com/search?format=rss&q=test'})
            return httpx.Response(200, stream=ChunkStream(b'<rss><channel/></rss>'))
        client = httpx.AsyncClient
        with patch.object(web_search, 'resolve_public_ip', AsyncMock(return_value='93.184.216.34')) as dns, patch.object(web_search.httpx, 'AsyncClient', lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs)):
            await web_search.request_search('https://www.bing.com/search?q=test', free_redirects=True)
        self.assertEqual(dns.await_count, 2)
        self.assertEqual(requests[1].headers['Host'], 'cn.bing.com')

    async def test_cache_limits_and_search_to_read_chain(self):
        found = [{'title': 'Docs', 'url': 'https://example.com/article', 'snippet': 'Search snippet'}]
        context = ToolContext(web_search_enabled=True, history=[SimpleNamespace(role='user', content='请联网搜索官方文档')])
        with patch.object(web_search, 'search_provider', AsyncMock(side_effect=[found, [{**found[0], 'url': 'https://example.com/second'}], [{**found[0], 'url': 'https://example.com/third'}]])) as provider:
            first = await execute_tool('search_web', '{"query":"official docs"}', context=context)
            self.assertEqual(first, await execute_tool('search_web', '{"query":"official docs"}', context=context))
            await execute_tool('search_web', '{"query":"second"}', context=context)
            await execute_tool('search_web', '{"query":"third"}', context=context)
            with self.assertRaisesRegex(ValueError, '最多'):
                await execute_tool('search_web', '{"query":"fourth"}', context=context)
        self.assertEqual(provider.await_count, 3)
        self.assertNotIn('citation_id', add_citation_ids('search_web', first, {}, set()))
        message = SimpleNamespace(role='assistant', status='completed', output=[{'type': 'function_call', 'call_id': 's', 'name': 'search_web'}, {'type': 'function_call_output', 'call_id': 's', 'output': first}])
        self.assertEqual(collect_history_sources([message]), {})
        document = {'title': 'Docs', 'url': found[0]['url'], 'text': 'Actual page content', 'retrieved_at': 100}
        with patch.object(webpage, 'fetch_page', AsyncMock(return_value=document)):
            raw = await execute_tool('read_webpage', '{"url":"https://example.com/article"}', context=context)
            next_turn = ToolContext(history=[message])
            await execute_tool('read_webpage', '{"url":"https://example.com/article"}', context=next_turn)
            with self.assertRaisesRegex(ValueError, '不能自行拼接'):
                await execute_tool('read_webpage', '{"url":"https://example.com/article?leak=secret"}', context=next_turn)
        self.assertEqual(json.loads(add_citation_ids('read_webpage', raw, {}, set()))['chunks'][0]['citation_id'], 1)

    async def test_platform_homepages_do_not_match_topic_and_repeated_results_stop(self):
        found = [{'title': '哔哩哔哩 bilibili 首页', 'url': 'https://www.bilibili.com/', 'snippet': '关于我们 下载客户端'}]
        self.assertEqual(web_search.assess_search_results('bilibili 电影推荐', found), 'low_relevance')
        self.assertEqual(web_search.assess_search_results('bilibili 电影推荐', [{**found[0], 'url': 'https://www.bilibili.com/movie', 'title': '哔哩哔哩电影频道'}]), 'topic_matched')
        context = ToolContext(web_search_enabled=True)
        with patch.object(web_search, 'search_provider', AsyncMock(return_value=found)) as provider:
            await execute_tool('search_web', '{"query":"bilibili 电影推荐"}', context=context)
            result = json.loads(await execute_tool('search_web', '{"query":"B站 电影有哪些"}', context=context))
            self.assertTrue(result['stop_searching'])
            self.assertEqual(result['new_results'], 0)
            with self.assertRaisesRegex(ValueError, '重复'):
                await execute_tool('search_web', '{"query":"哔哩哔哩 电影"}', context=context)
        self.assertEqual(provider.await_count, 2)

    async def test_disabled_and_malformed_results_are_explicit(self):
        self.config.web_search_enabled = False
        self.assertNotIn('search_web', [item['function']['name'] for item in get_available_tools(set())])
        with self.assertRaisesRegex(ValueError, '权限'):
            await execute_tool('search_web', '{"query":"test"}', context=ToolContext(web_search_enabled=True))
        self.config.web_search_enabled = True
        self.config.web_search_provider = 'duckduckgo'
        with patch.object(web_search, 'request_search', AsyncMock(return_value=b'<html>Verify you are human</html>')):
            with self.assertRaisesRegex(ValueError, '访问限制'):
                await web_search.search_provider('query', 5)
