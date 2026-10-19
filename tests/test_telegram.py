"""Telegram Bot API 模拟服务：确定性验证，不发送真实外部消息。"""
import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from codex_user_bridge.adapters import LocalHttpAdapter
from codex_user_bridge.service import make_server
from codex_user_bridge.store import Store
from codex_user_bridge.telegram import TelegramApi, TelegramChannel, TelegramError
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

TOKEN = '123456:TEST_TOKEN_NOT_A_REAL_CREDENTIAL'


class TelegramTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'messages.db')
        self.calls, self.updates = [], []
        self.error, self.webhook = None, ''
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass  # Bot API 路径包含凭据，连模拟日志也不输出该路径。

            def do_POST(self):
                method = self.path.rsplit('/', 1)[-1]
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                outer.calls.append((method, body))
                status = 200
                if outer.error and method == 'sendMessage':
                    result = outer.error
                    status = result.get('error_code', 400)
                else:
                    if method == 'getMe':
                        value = {'id': 123456, 'is_bot': True, 'username': 'test_bot'}
                    elif method == 'getWebhookInfo':
                        value = {'url': outer.webhook}
                    elif method == 'sendMessage':
                        value = {'message_id': len(outer.calls), 'chat': {'id': body['chat_id']}}
                    elif method == 'getUpdates':
                        value = [u for u in outer.updates if u['update_id'] >= body['offset']]
                    else:
                        value = True
                    result = {'ok': True, 'result': value}
                data = json.dumps(result).encode()
                self.send_response(status)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.fake = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.fake.serve_forever, daemon=True)
        self.thread.start()
        self.api = TelegramApi(TOKEN, f'http://127.0.0.1:{self.fake.server_port}')
        self.channel = TelegramChannel(self.store, self.api, 42, 42)

    def tearDown(self):
        self.store.lease(self.channel.owner, release=True)
        self.fake.shutdown()
        self.fake.server_close()
        self.thread.join(timeout=3)
        self.temp.cleanup()

    def question(self, request_id='r1'):
        return self.store.send('task', request_id, '测试问题', ['继续', '稍后'], 'telegram')

    def incoming(self, update_id, text='允许本次测试', sender=42, chat=42, quoted=None):
        message = {'text': text, 'from': {'id': sender, 'is_bot': False},
                   'chat': {'id': chat, 'type': 'private'}}
        if quoted is not None:
            message['reply_to_message'] = {'message_id': quoted}
        return {'update_id': update_id, 'message': message}

    def test_send_quote_reply_and_replay(self):
        self.channel.start()
        q = self.question()
        self.assertEqual(q['delivery_status'], 'queued')
        self.assertFalse(q['is_simulated'])
        self.channel.deliver_one()
        mid = self.store.get('task', q['question_id'])
        self.assertEqual(mid['delivery_status'], 'sent')
        with self.store.connect() as db:
            message_id = db.execute('SELECT telegram_message_id FROM questions').fetchone()[0]
        self.updates = [self.incoming(10, quoted=message_id)]
        self.channel.receive()
        answer = self.store.get('task', q['question_id'])
        self.assertEqual(answer['reply']['source'], 'telegram')
        self.assertEqual(answer['reply']['sender_id'], 42)
        self.assertFalse(answer['reply']['is_simulated'])
        self.assertEqual(self.store.offset(), 11)
        self.updates.append(self.incoming(11, text='不能覆盖', quoted=message_id))
        self.channel.receive()
        self.assertEqual(self.store.get('task', q['question_id'])['reply']['text'], '允许本次测试')
        self.assertEqual(Store(self.store.path).offset(), 12)

    def test_unauthorized_and_unrelated_messages_ignored(self):
        q = self.question()
        command = f"/reply {q['question_id']} 回答"
        self.updates = [self.incoming(1, command, sender=99),
                        self.incoming(2, command, chat=99), self.incoming(3, '无关联回答')]
        self.channel.receive()
        self.assertEqual(self.store.get('task', q['question_id'])['status'], 'pending')
        self.assertEqual(self.store.offset(), 4)

    def test_question_id_reply_routes_multiple_tasks(self):
        first, second = self.question('r1'), self.question('r2')
        self.updates = [self.incoming(1, f"/reply {second['question_id']} 只回答第二个")]
        self.channel.receive()
        self.assertEqual(self.store.get('task', first['question_id'])['status'], 'pending')
        self.assertEqual(self.store.get('task', second['question_id'])['reply']['text'], '只回答第二个')

    def test_same_request_only_sent_once_and_mock_does_not_answer_telegram(self):
        first, again = self.question(), self.question()
        self.assertEqual(first['question_id'], again['question_id'])
        self.store.auto_reply(0, '禁止模拟回答')
        self.channel.deliver_one()
        self.channel.deliver_one()
        self.assertEqual(sum(method == 'sendMessage' for method, _ in self.calls), 1)
        self.assertIsNone(self.store.get('task', first['question_id'])['reply'])

    def test_unknown_send_not_retried_or_leaked(self):
        q = self.question()
        self.error = {'ok': False, 'error_code': 502, 'description': 'https://api.telegram.org/bot' + TOKEN}
        self.channel.deliver_one()
        result = self.store.get('task', q['question_id'])
        self.assertEqual(result['delivery_status'], 'unknown')
        self.assertNotIn(TOKEN, result['delivery_error'])
        self.channel.deliver_one()
        self.assertEqual(sum(method == 'sendMessage' for method, _ in self.calls), 1)

    def test_rate_limit_persists_cooldown(self):
        q = self.question()
        self.error = {'ok': False, 'error_code': 429, 'parameters': {'retry_after': 60}}
        self.assertEqual(self.channel.deliver_one(), 60)
        self.assertEqual(self.store.get('task', q['question_id'])['delivery_status'], 'queued')
        self.assertIsNone(Store(self.store.path).claim_outgoing())

    def test_webhook_refused_and_single_consumer(self):
        self.webhook = 'https://example.com/hook'
        with self.assertRaisesRegex(ValueError, 'webhook'):
            self.channel.start()
        self.webhook = ''
        self.channel.start()
        other = TelegramChannel(self.store, self.api, 42, 42)
        with self.assertRaisesRegex(ValueError, '重复'):
            other.start()

    def test_recovery_and_identity_binding(self):
        q = self.question()
        self.store.claim_outgoing()
        self.channel.start()
        self.assertEqual(self.store.get('task', q['question_id'])['delivery_status'], 'unknown')
        with self.assertRaisesRegex(ValueError, '绑定'):
            TelegramChannel(self.store, self.api, 43, 43)

    def test_old_database_migrates(self):
        path = Path(self.temp.name) / 'old.db'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE questions (question_id TEXT PRIMARY KEY,task_id TEXT,request_id TEXT,'
                       'message TEXT,options TEXT,created_at REAL,reply TEXT,answered_at REAL,UNIQUE(task_id,request_id))')
            db.execute("INSERT INTO questions VALUES ('old','task','r','old message','[]',1,'mock reply',2)")
        db.close()
        result = Store(path).get('task', 'old')
        self.assertEqual(result['reply']['source'], 'local_mock')
        self.assertTrue(result['reply']['is_simulated'])

    def test_long_question_fails_without_silent_truncation(self):
        q = self.store.send('task', 'long', '🙂' * 2100, [], 'telegram')
        self.channel.deliver_one()
        self.assertEqual(self.store.get('task', q['question_id'])['delivery_status'], 'failed')
        self.assertFalse(any(method == 'sendMessage' for method, _ in self.calls))

    def test_real_mcp_through_http_to_telegram_api(self):
        server = make_server(self.store, 0, channel='telegram')
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        async def run():
            params = StdioServerParameters(command=sys.executable, args=['-m', 'codex_user_bridge.mcp_server'],
                env={'BRIDGE_BASE_URL': f'http://127.0.0.1:{server.server_port}'})
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    sent = await client.call_tool('bridge_send_question', {
                        'task_id': 'task', 'request_id': 'mcp', 'message': 'MCP 到 Telegram'})
                    self.assertFalse(sent.isError)
                    q = sent.structuredContent
                    self.assertEqual(q['delivery_status'], 'queued')
                    self.channel.deliver_one()
                    self.updates = [self.incoming(1, f"/reply {q['question_id']} 真人测试回答")]
                    self.channel.receive()
                    result = await client.call_tool('bridge_get_reply', {'task_id': 'task', 'question_id': q['question_id']})
                    self.assertFalse(result.isError)
                    self.assertEqual(result.structuredContent['reply']['text'], '真人测试回答')
                    self.assertFalse(result.structuredContent['reply']['is_simulated'])
        try:
            asyncio.run(run())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == '__main__':
    unittest.main(verbosity=2)
