import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from backend.app.tools.runtime import ToolContext
from backend.app.tools.web_scope import requested_origins, within_requested_site
from backend.app.tools.local import read_webpage as webpage, search_web
from backend.app.tools.registry import execute_tool
from backend.app.chat.research import assess_result
from backend.app.chat.citations import missing_source_footer


class ResearchFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_sites_allow_document_paths_without_granting_other_hosts_or_queries(self):
        history = [SimpleNamespace(role='user', content='查阅 https://docs.sample.example/ 的文档')]
        origins = requested_origins(history)
        context = ToolContext(history=history, web_origins=origins, is_subagent=True)
        url = 'https://docs.sample.example/guide/start.html'
        self.assertTrue(within_requested_site(context, url))
        for rejected in ('https://evil.example/guide', url + '?data=private', 'https://docs.sample.example.evil.example/guide', 'http://127.0.0.1/private'):
            self.assertFalse(within_requested_site(context, rejected))
        document = {'url': url, 'title': '文档', 'text': '教程目录', 'retrieved_at': 1}
        with patch.object(webpage, 'fetch_page', AsyncMock(return_value=document)) as fetch:
            result = json.loads(await execute_tool('read_webpage', json.dumps({'url': url}), context=context))
            self.assertEqual(result['chunks'][0]['content'], document['text'])
            fetch.assert_awaited_once()
        self.assertFalse(requested_origins([SimpleNamespace(role='assistant', content='请读取 evil.example')]))
        self.assertFalse(requested_origins([SimpleNamespace(role='user', content='查阅 Python 官方教程和 MDN')]))
        discovered = {'https://another.example/home'}
        self.assertTrue(within_requested_site(context, 'https://another.example/guide', discovered))
        self.assertFalse(within_requested_site(context, 'https://another.example/guide'))

    async def test_navigation_keeps_javascript_entry_after_many_css_links(self):
        html = '<nav>' + ''.join(f'<a href="/en-US/docs/Web/CSS/Reference/{i}">CSS {i}</a>' for i in range(70))
        html += '<a href="/en-US/docs/Learn_web_development/Core/Scripting">JavaScript 入门</a></nav>'
        links = webpage.page_links(html, 'https://developer.mozilla.org/')
        self.assertLessEqual(len(links), 30)
        self.assertTrue(any(link['title'] == 'JavaScript 入门' for link in links))

    async def test_source_presence_does_not_override_explicit_evidence_gaps(self):
        message = SimpleNamespace(status='completed', content='已核实的信息有限，不足以给出 MDN 官方域名的完整 JavaScript 入门顺序。', output=[
            {'type': 'function_call', 'call_id': 'r', 'name': 'read_webpage'},
            {'type': 'function_call_output', 'call_id': 'r', 'output': json.dumps({'chunks': [{'citation_id': 1}, {'citation_id': 2}]})}])
        result = assess_result(message)
        self.assertEqual(result['outcome'], 'insufficient_evidence')
        self.assertEqual(result['source_count'], 2)
        self.assertIn('不足以', result['limitations'])

    async def test_homepage_and_different_domain_do_not_meet_explicit_site_query(self):
        found = [{'url': 'https://docs.sample.example/', 'title': '文档首页', 'snippet': 'guides and tutorials'},
                 {'url': 'https://mirror.example/guide', 'title': '教程镜像', 'snippet': 'tutorial'}]
        self.assertEqual(search_web.assess_search_results('docs.sample.example tutorial guide', found), 'low_relevance')
        self.assertEqual(search_web.provider_query('docs.python.org/3/tutorial/index.html The Python Tutorial'), 'Python Tutorial')
        footer = missing_source_footer('已读取两篇资料，但尚不能核实全部结论。', [2, 1])
        self.assertIn('[1] [2]', footer)
        self.assertEqual(missing_source_footer('有依据[1]', [1]), '')
