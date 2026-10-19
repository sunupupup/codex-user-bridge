"""飞书 REST 模拟、官方 SDK 事件模型与真实 MCP 子进程。"""
import asyncio
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from codex_user_bridge.feishu import FeishuApi, FeishuChannel
from codex_user_bridge.feishu_receiver import exchange
from codex_user_bridge.service import make_server
from codex_user_bridge.store import Store
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class FeishuTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'messages.db')
        self.calls, self.failure = [], 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                outer.calls.append((self.path, body, self.headers.get('Authorization')))
                if 'tenant_access_token' in self.path:
                    result = {'code': 0, 'tenant_access_token': 'fake_access', 'expire': 7200}
                else:
                    result = {'code': outer.failure, 'msg': 'do not echo secret', 'data': {'message_id': 'om_test'}}
                data = json.dumps(result).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.api = FeishuApi('cli_test', 'fake_secret', f'http://127.0.0.1:{self.server.server_port}')
        self.channel = FeishuChannel(self.store, self.api, 'ou_me')

    def tearDown(self):
        self.channel.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temp.cleanup()

    def question(self, channel='feishu', request='r'):
        return self.store.send('task', request, '测试', ['继续'], channel)

    def event(self, text='回答', user='ou_me', app='cli_test', parent='om_test', mid='om_reply', kind='p2p'):
        return {'schema': '2.0', 'header': {'app_id': app, 'event_id': 'event', 'event_type': 'im.message.receive_v1'},
                'event': {'sender': {'sender_type': 'user', 'sender_id': {'open_id': user}},
                          'message': {'message_id': mid, 'parent_id': parent, 'chat_type': kind,
                                      'message_type': 'text', 'content': json.dumps({'text': text})}}}

    def test_send_and_quote_reply(self):
        self.channel.start()
        q = self.question()
        self.channel.deliver_one()
        self.channel.accept(self.event())
        result = self.store.get('task', q['question_id'])
        self.assertEqual(result['delivery_status'], 'sent')
        self.assertEqual(result['reply']['source'], 'feishu')
        self.assertEqual(result['reply']['sender_id'], 'ou_me')
        self.assertFalse(result['is_simulated'])
        self.assertEqual(self.calls[-1][2], 'Bearer fake_access')

    def test_id_reply_dedup_and_other_channels_unchanged(self):
        q = self.question()
        other = self.question('telegram', 'other')
        self.channel.accept(self.event(f"/reply {q['question_id']} 第一答", parent=None))
        self.channel.accept(self.event(f"/reply {q['question_id']} 重投改答", parent=None))
        self.channel.accept(self.event(f"/reply {other['question_id']} 错渠道", mid='new'))
        self.assertEqual(self.store.get('task', q['question_id'])['reply']['text'], '第一答')
        self.assertIsNone(self.store.get('task', other['question_id'])['reply'])

    def test_wrong_sender_app_group_and_unrelated_text(self):
        q = self.question()
        for i, e in enumerate([self.event(user='ou_other'), self.event(app='wrong'),
                                self.event(kind='group'), self.event(parent=None)]):
            e['event']['message']['message_id'] = str(i)
            self.channel.accept(e)
        self.assertIsNone(self.store.get('task', q['question_id'])['reply'])

    def test_api_error_does_not_repeat_or_leak(self):
        self.failure = 99991663
        q = self.question()
        self.channel.deliver_one()
        self.channel.deliver_one()
        result = self.store.get('task', q['question_id'])
        self.assertEqual(result['delivery_status'], 'failed')
        self.assertNotIn('secret', result['delivery_error'])
        self.assertEqual(len(self.calls), 2)  # 一次取 token，一次发送。

    def test_independent_lease_and_recovery(self):
        q = self.question()
        self.store.claim_outgoing('feishu')
        self.channel.start()
        self.assertEqual(self.store.get('task', q['question_id'])['delivery_status'], 'unknown')
        self.assertTrue(self.store.lease('telegram-owner'))
        self.store.lease('telegram-owner', release=True)
        other = FeishuChannel(self.store, self.api, 'ou_me')
        with self.assertRaises(ValueError):
            other.start()

    def test_official_sdk_model_and_ack(self):
        import lark_oapi as lark
        from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
        q = self.question()
        event = json.loads(lark.JSON.marshal(P2ImMessageReceiveV1(self.event(f"/reply {q['question_id']} SDK"))))
        output = io.StringIO()
        exchange(event, output, io.StringIO('ok\n'))
        self.channel.accept(json.loads(output.getvalue()))
        self.assertEqual(self.store.get('task', q['question_id'])['reply']['text'], 'SDK')
        with self.assertRaises(RuntimeError):
            exchange(event, io.StringIO(), io.StringIO('retry\n'))
        handler = lark.EventDispatcherHandler.builder('', '').register_p2_im_message_receive_v1(lambda _: None).build()
        self.assertIsNotNone(handler)
        self.assertTrue(callable(lark.ws.Client.start))
        # SDK 导入时创建全局 loop；测试不连网，在此释放它，真实接收端由独立进程持有。
        from lark_oapi.ws.client import loop
        if not loop.is_running() and not loop.is_closed():
            loop.close()

    def test_mcp_end_to_end(self):
        server = make_server(self.store, 0, channel='feishu')
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        async def run():
            params = StdioServerParameters(command=sys.executable, args=['-m', 'codex_user_bridge.mcp_server'],
                env={'BRIDGE_BASE_URL': f'http://127.0.0.1:{server.server_port}'})
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    result = await client.call_tool('bridge_send_question', {'task_id': 'mcp', 'request_id': 'r', 'message': '飞书'})
                    self.assertFalse(result.isError)
                    q = result.structuredContent
                    self.channel.deliver_one()
                    self.channel.accept(self.event(f"/reply {q['question_id']} 飞书回复"))
                    answer = await client.call_tool('bridge_get_reply', {'task_id': 'mcp', 'question_id': q['question_id']})
                    self.assertFalse(answer.isError)
                    self.assertEqual(answer.structuredContent['reply']['source'], 'feishu')
        try:
            asyncio.run(run())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
