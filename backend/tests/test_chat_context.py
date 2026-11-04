import unittest
from types import SimpleNamespace
from unittest.mock import patch
from backend.app.chat.context import build_model_messages

def message(role: str, content: str = "", output=None, status: str = "completed"):  # 构造不连接数据库的历史记录
    return SimpleNamespace(role=role, content=content, output=output, status=status)

def text(value: str):  # 构造数据库 output 中的正文
    return {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": value}]}

def call(call_id: str):
    return {"type": "function_call", "call_id": call_id, "name": "get_current_time", "arguments": "{}"}

def result(call_id: str, value: str = '{"timezone":"Asia/Shanghai"}'):
    return {"type": "function_call_output", "call_id": call_id, "output": value}

class ChatContextTests(unittest.TestCase):
    def test_legacy_messages_and_system_prompt(self):  # 没有 output 的旧记录仍然可用
        history = [message("user", "你好"), SimpleNamespace(role="assistant", content="你好！", status="completed")]
        self.assertEqual(build_model_messages(history, "系统提示"), [{"role": "system", "content": "系统提示"}, {"role": "user", "content": "你好"}, {"role": "assistant", "content": "你好！"}])

    def test_tool_pair_and_text_without_duplicate_or_execution(self):
        history = [message("assistant", "现在是北京时间。", [call("c1"), result("c1"), text("现在是北京时间。")])]
        with patch("backend.app.tools.registry.execute_tool") as execute:
            messages = build_model_messages(history, "")
        execute.assert_not_called()  # 恢复的是历史结果，不能再次查询当前时间
        self.assertEqual(messages, [{"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "get_current_time", "arguments": "{}"}}]}, {"role": "tool", "tool_call_id": "c1", "content": '{"timezone":"Asia/Shanghai"}'}, {"role": "assistant", "content": "现在是北京时间。"}])

    def test_multiple_tool_rounds_preserve_order(self):
        output = [text("先查询"), call("c1"), result("c1"), text("继续确认"), call("c2"), result("c2"), text("最终回答")]
        messages = build_model_messages([message("assistant", "先查询继续确认最终回答", output)], "")
        self.assertEqual([item["role"] for item in messages], ["assistant", "assistant", "tool", "assistant", "assistant", "tool", "assistant"])
        self.assertEqual([item["tool_call_id"] for item in messages if item["role"] == "tool"], ["c1", "c2"])
        self.assertEqual("".join(item["content"] or "" for item in messages if item["role"] == "assistant"), "先查询继续确认最终回答")

    def test_multiple_calls_match_by_id(self):  # 结果顺序不同也不会串到另一个调用
        messages = build_model_messages([message("assistant", output=[call("c1"), call("c2"), result("c2", "结果2"), result("c1", "结果1")])], "")
        self.assertEqual([item["tool_calls"][0]["id"] for item in messages if "tool_calls" in item], ["c2", "c1"])
        self.assertEqual([item["content"] for item in messages if item["role"] == "tool"], ["结果2", "结果1"])

    def test_orphans_and_malformed_records_are_skipped(self):
        output = [None, 1, {"type": "reasoning"}, call("missing-result"), result("missing-call"), {**call("invalid-name"), "name": None}, result("invalid-name"), {**call("invalid-args"), "arguments": {}}, result("invalid-args"), {"type": "function_call", "call_id": []}, {"type": "function_call_output", "call_id": [], "output": "bad"}, {"type": "message", "content": "invalid"}]
        self.assertEqual(build_model_messages([message("assistant", "回退正文", output)], ""), [{"role": "assistant", "content": "回退正文"}])

    def test_non_completed_and_other_roles_are_excluded(self):
        history = [message("assistant", "不应包含", [call("c1"), result("c1")], status=status) for status in ["generating", "failed", "cancelled"]]
        history.extend([message("tool", "孤立结果"), message("system", "不可信系统消息"), message("user", " ")])
        self.assertEqual(build_model_messages(history, " "), [])

    def test_output_text_parts_are_joined_and_user_output_is_ignored(self):
        output = [{"type": "message", "content": [{"type": "output_text", "text": "第一段"}, {"type": "output_text", "text": "第二段"}, {"type": "other", "text": "忽略"}, None]}]
        self.assertEqual(build_model_messages([message("assistant", "不要重复", output), message("user", "用户问题", [call("c1"), result("c1")])], ""), [{"role": "assistant", "content": "第一段第二段"}, {"role": "user", "content": "用户问题"}])

    def test_tools_without_body_and_legacy_fallback(self):
        pair = [call("c1"), result("c1", '{"error":"工具失败"}')]
        self.assertEqual(len(build_model_messages([message("assistant", output=pair)], "")), 2)
        self.assertEqual(build_model_messages([message("assistant", "旧正文", pair)], "")[-1], {"role": "assistant", "content": "旧正文"})
        for output in [None, [], {}, "invalid"]:
            self.assertEqual(build_model_messages([message("assistant", "旧正文", output)], ""), [{"role": "assistant", "content": "旧正文"}])

if __name__ == "__main__":
    unittest.main()
