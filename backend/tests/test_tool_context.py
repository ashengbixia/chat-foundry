import json
import unittest
from backend.app.chat.tool_context import project_tool_results, prepare_model_request
from backend.app.llm.budget import InputBudgetError
from backend.app.llm.budget import estimate_input_tokens
from backend.app.rag.chunking import split_with_tokenizer

class CharacterTokenizer:
    def num_special_tokens_to_add(self, pair=False):
        return 2

    def __call__(self, text, **kwargs):
        return {"offset_mapping": [(index, index + 1) for index in range(len(text))]}

class ToolContextTests(unittest.TestCase):
    def test_final_phase_preserves_required_reasoning_and_original_chain(self):
        reasoning = '模型服务要求原样回传的思考' * 100
        raw = json.dumps({'task_id': 't', 'status': 'completed', 'outcome': 'answered', 'summary': '结论', 'chunks': []})
        messages = [{'role': 'user', 'content': '比较两个任务'},
                    {'role': 'assistant', 'reasoning_content': reasoning, 'tool_calls': [{'id': 't', 'function': {'name': 'delegate_task', 'arguments': '{}'}}]},
                    {'role': 'tool', 'tool_call_id': 't', 'content': raw}]
        tools = [{'type': 'function', 'function': {'name': 'delegate_task', 'description': '工具说明' * 2000}}]
        projected, offered, final, reason = prepare_model_request(messages, {'t': raw}, tools, 12000, 1000, 100)
        self.assertIsNone(offered)
        self.assertTrue(final)
        self.assertEqual(reason, 'input_budget')
        self.assertEqual(messages[1]['reasoning_content'], reasoning)
        self.assertEqual(next(item['reasoning_content'] for item in projected if 'reasoning_content' in item), reasoning)
        self.assertIn('结论', json.dumps(projected, ensure_ascii=False))
        self.assertLessEqual(estimate_input_tokens(projected), 10900)
        with self.assertRaises(InputBudgetError):
            prepare_model_request(messages, {'t': raw}, tools, 1000, 200, 100)

    def test_search_budget_compacts_snippets_without_changing_saved_results(self):
        data = {'status': 'ok', 'query': 'Python', 'results': [{'title': 'Docs', 'url': 'https://example.com/docs', 'snippet': '摘要' * 2000}, {'title': 'Guide', 'url': 'https://example.com/guide', 'snippet': '片段' * 2000}]}
        raw = json.dumps(data, ensure_ascii=False)
        messages = [{'role': 'user', 'content': '搜索'}, {'role': 'assistant', 'tool_calls': [{'id': 'search', 'function': {'name': 'search_web', 'arguments': '{}'}}]}, {'role': 'tool', 'tool_call_id': 'search', 'content': raw}]
        result = json.loads(project_tool_results(messages, {'search': raw}, [], 1800, 200, 100)[-1]['content'])
        self.assertTrue(result['context_limited'])
        self.assertEqual([item['url'] for item in result['results']], [item['url'] for item in data['results']])
        self.assertLess(len(result['results'][0]['snippet']), 4000)
        self.assertEqual(len(json.loads(raw)['results'][0]['snippet']), 4000)
        self.assertNotIn('citation_id', json.dumps(result))

    def test_webpage_budget_projection_keeps_next_offset_at_visible_end(self):
        data = {'url': 'https://example.com', 'total_chars': 5000, 'next_offset': 4000, 'has_more': True, 'chunks': [{'source_type': 'web', 'url': 'https://example.com', 'title': '文章', 'start_offset': 0, 'end_offset': 4000, 'content': '正文' * 2000}]}
        raw = json.dumps(data, ensure_ascii=False)
        messages = [{'role': 'user', 'content': '总结网页'}, {'role': 'assistant', 'tool_calls': [{'id': 'web', 'function': {'name': 'read_webpage', 'arguments': '{}'}}]}, {'role': 'tool', 'tool_call_id': 'web', 'content': raw}]
        projected = project_tool_results(messages, {'web': raw}, [], 1800, 200, 100)
        result = json.loads(projected[-1]['content'])
        self.assertLess(len(result['chunks'][0]['content']), 4000)
        self.assertEqual(result['next_offset'], result['chunks'][0]['end_offset'])
        self.assertTrue(result['has_more'])
        self.assertEqual(json.loads(raw)['next_offset'], 4000)

    def test_token_windows_cover_document_and_keep_offsets(self):
        text = "中" * 814 + "FT-204：泵出口流量。" + "文" * 1500
        chunks = split_with_tokenizer(text, CharacterTokenizer(), 512)
        covered = set()
        for chunk in chunks:
            self.assertEqual(chunk["content"], text[chunk["start_offset"]:chunk["end_offset"]])
            self.assertLessEqual(len(chunk["content"]) + 2, 512)
            covered.update(range(chunk["start_offset"], chunk["end_offset"]))
        self.assertEqual(len(covered), len(text))
        self.assertTrue(any("FT-204：泵出口流量。" in chunk["content"] for chunk in chunks))

    def make_messages(self):
        chunk = {"file_id": "f", "chunk_id": "c", "start_offset": 0, "end_offset": 3000, "content": "正文" * 1500}
        raw = {call: json.dumps({"status": "ok", "chunks": [chunk]}, ensure_ascii=False) for call in ("a", "b")}
        user = {"role": "user", "content": "FT-204是什么"}
        messages = [user]
        for call in raw:
            messages.extend([{"role": "assistant", "tool_calls": [{"id": call, "function": {"name": "search_uploaded_files", "arguments": "{}"}}]}, {"role": "tool", "tool_call_id": call, "content": raw[call]}])
        return user, messages, raw

    def test_dedup_preserves_original_and_user_identity(self):
        user, messages, raw = self.make_messages()
        result = project_tool_results(messages, raw, None, 20000, 100, 100)
        self.assertIs(result[0], user)
        second = json.loads(result[-1]["content"])
        self.assertEqual(second["chunks"], [])
        self.assertEqual(second["status"], "ok")
        self.assertEqual(len(second["omitted_duplicate_chunks"]), 1)
        self.assertEqual(messages[-1]["content"], raw["b"])
        # 重复投影仍从完整结果重算，不能把上轮截短内容当成原始正文。
        again = project_tool_results(result, raw, None, 20000, 100, 100)
        self.assertEqual(result, again)

    def test_budget_shortening_updates_offsets_and_keeps_call_pairs(self):
        user, messages, raw = self.make_messages()
        result = project_tool_results(messages, raw, None, 4000, 100, 100)
        self.assertLessEqual(estimate_input_tokens(result), 3800)
        data = json.loads(result[2]["content"])
        chunk = data["chunks"][0]
        self.assertTrue(chunk["truncated"])
        self.assertTrue(data["context_limited"])
        self.assertEqual(chunk["end_offset"], len(chunk["content"]))
        self.assertEqual(len(result), len(messages))
        self.assertEqual(json.loads(raw["a"])["chunks"][0]["end_offset"], 3000)
