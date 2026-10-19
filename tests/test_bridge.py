"""真实本地 HTTP + 真实 MCP stdio 子进程；不调用任何模型 API。"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from codex_user_bridge.adapters import LocalHttpAdapter
from codex_user_bridge.service import DEFAULT_REPLY, make_server, reply_worker
from codex_user_bridge.store import Store
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'messages.sqlite3')
        self.server = make_server(self.store, 0)
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temp.cleanup()

    def request(self, path, payload=None, headers=None):
        data = None if payload is None else json.dumps(payload).encode()
        req = Request(self.base + path, data=data, headers=headers or {'Content-Type': 'application/json'})
        with build_opener(ProxyHandler({})).open(req, timeout=3) as response:
            return json.load(response)

    def send(self, request_id='r1', message='需要模拟测试吗？'):
        return self.request('/chat', {'task_id': 'task1', 'request_id': request_id,
                                      'message': message, 'options': ['继续', '稍后']})

    def test_send_pending_then_background_reply(self):
        sent = self.send()
        self.assertEqual(sent['status'], 'pending')
        stop = threading.Event()
        worker = threading.Thread(target=reply_worker, args=(self.store, stop, .03, .05))
        worker.start()
        try:
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                reply = self.request('/chat/' + sent['question_id'] + '?task_id=task1')
                if reply['status'] == 'answered':
                    break
                time.sleep(.03)
            self.assertEqual(reply['reply']['text'], DEFAULT_REPLY)
            self.assertTrue(reply['reply']['is_simulated'])
            self.assertEqual(reply['reply']['source'], 'local_mock')
            self.assertEqual(reply, self.request('/chat/' + sent['question_id'] + '?task_id=task1'))
        finally:
            stop.set()
            worker.join(timeout=2)

    def test_concurrent_retries_do_not_duplicate(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: self.send(), range(12)))
        self.assertEqual(len({r['question_id'] for r in results}), 1)

    def test_reuse_key_with_changed_message_conflicts(self):
        self.send()
        with self.assertRaises(HTTPError) as caught:
            self.send(message='换一个问题')
        self.assertEqual(caught.exception.code, 409)
        caught.exception.close()

    def test_task_isolation_and_missing_question(self):
        sent = self.send()
        for path in ('/chat/' + sent['question_id'] + '?task_id=other', '/chat/missing?task_id=task1'):
            with self.assertRaises(HTTPError) as caught:
                self.request(path)
            self.assertEqual(caught.exception.code, 404)
            caught.exception.close()

    def test_restart_preserves_questions_and_answers(self):
        sent = self.send()
        # 重建存储实例等价于服务重启后的数据库打开路径。
        reopened = Store(self.store.path)
        self.assertEqual(reopened.get('task1', sent['question_id'])['status'], 'pending')
        reopened.auto_reply(0, DEFAULT_REPLY)
        self.assertEqual(Store(self.store.path).get('task1', sent['question_id'])['status'], 'answered')
        self.assertEqual(reopened.auto_reply(0, '不得覆盖'), 0)

    def test_invalid_payloads_are_rejected(self):
        for payload in ([], {}, {'task_id': 't', 'request_id': 'r', 'message': '  '},
                        {'task_id': 't', 'request_id': 'r', 'message': 'x', 'options': [3]}):
            with self.assertRaises(HTTPError) as caught:
                self.request('/chat', payload)
            self.assertEqual(caught.exception.code, 400)
            caught.exception.close()

    def test_cross_origin_and_non_json_rejected(self):
        for headers, expected in (({'Origin': 'https://example.com'}, 403),
                                  ({'Content-Type': 'text/plain'}, 415)):
            with self.assertRaises(HTTPError) as caught:
                self.request('/chat', {}, headers)
            self.assertEqual(caught.exception.code, expected)
            caught.exception.close()

    def test_adapter_refuses_remote_address(self):
        for base in ('https://example.com', 'http://127.0.0.1:5555/other'):
            with self.assertRaises(ValueError):
                LocalHttpAdapter(base)

    def test_unavailable_service_reports_unknown_result(self):
        async def scenario():
            with self.assertRaisesRegex(RuntimeError, '未知'):
                await LocalHttpAdapter('http://127.0.0.1:0').send('t', 'r', 'x', [])
        asyncio.run(scenario())

    def test_mcp_process_end_to_end(self):
        async def scenario():
            params = StdioServerParameters(command=sys.executable,
                args=['-m', 'codex_user_bridge.mcp_server'], env={'BRIDGE_BASE_URL': self.base})
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    tools = await client.list_tools()
                    self.assertEqual({t.name for t in tools.tools}, {'bridge_send_question', 'bridge_get_reply'})
                    for tool in tools.tools:
                        self.assertIsNotNone(tool.outputSchema)
                    sent = await client.call_tool('bridge_send_question', {
                        'task_id': 'mcp-task', 'request_id': 'mcp-r1', 'message': '协议测试', 'options': []})
                    self.assertFalse(sent.isError)
                    question = sent.structuredContent
                    self.assertEqual(question['status'], 'pending')
                    args = {'task_id': 'mcp-task', 'question_id': question['question_id']}
                    pending = await client.call_tool('bridge_get_reply', args)
                    self.assertEqual(pending.structuredContent['status'], 'pending')
                    self.store.auto_reply(0, DEFAULT_REPLY)
                    answered = await client.call_tool('bridge_get_reply', args)
                    self.assertTrue(answered.structuredContent['reply']['is_simulated'])
                    wrong = await client.call_tool('bridge_get_reply', {**args, 'task_id': 'other'})
                    self.assertTrue(wrong.isError)
                    invalid = await client.call_tool('bridge_send_question', {
                        'task_id': 'mcp-task', 'request_id': 'invalid', 'message': ''})
                    self.assertTrue(invalid.isError)
        asyncio.run(scenario())


if __name__ == '__main__':
    unittest.main(verbosity=2)
