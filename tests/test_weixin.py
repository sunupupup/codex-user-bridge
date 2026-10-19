"""微信真实 HTTP/MCP 链路的模拟平台测试，不需要个人账号凭据。"""
import asyncio
import base64
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from codex_user_bridge.channel_base import ChannelError
from codex_user_bridge.service import make_server
from codex_user_bridge.store import Store
from codex_user_bridge.weixin import WeixinApi, WeixinChannel, WeixinError, api_base, load_account, save_account
from codex_user_bridge.weixin_login import login
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class WeixinTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'messages.db')
        self.calls, self.messages = [], []
        self.send_result, self.poll_result = {'ret': 0}, None
        self.http_status = 200
        self.qr_status = 'confirmed'
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                self.handle_request()

            def do_POST(self):
                self.handle_request()

            def handle_request(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length']))) if self.command == 'POST' else None
                outer.calls.append((self.path, body, dict(self.headers.items())))
                if 'get_bot_qrcode' in self.path:
                    result = {'qrcode': 'fake_qr', 'qrcode_img_content': 'https://example.invalid/fake-login'}
                elif 'get_qrcode_status' in self.path:
                    result = {**outer.account, 'status': outer.qr_status}
                elif 'getupdates' in self.path:
                    result = outer.poll_result if outer.poll_result is not None else {
                        'ret': 0, 'msgs': outer.messages, 'get_updates_buf': 'cursor_next'}
                else:
                    result = outer.send_result
                data = json.dumps(result).encode()
                self.send_response(outer.http_status)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.account = {'bot_token': 'fake_weixin_token', 'ilink_bot_id': 'test_bot',
                        'ilink_user_id': 'test_user', 'baseurl': self.base}
        self.api = WeixinApi(self.account)
        self.channel = WeixinChannel(self.store, self.api)

    def tearDown(self):
        self.channel.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temp.cleanup()

    def question(self, channel='weixin', request='r'):
        return self.store.send('task', request, '是否继续测试？', ['继续'], channel)

    def event(self, text='你好', mid=1, user='test_user', context='context_one', **extra):
        return {'message_id': mid, 'from_user_id': user, 'to_user_id': 'test_bot',
                'message_type': 1, 'message_state': 2, 'context_token': context,
                'item_list': [{'type': 1, 'text_item': {'text': text}}], **extra}

    def accept(self, *events):
        self.channel.accept({'msgs': list(events), 'get_updates_buf': 'cursor_next'})

    def test_missing_context_queues_then_plain_message_activates(self):
        q = self.question()
        self.channel.deliver_one()
        result = self.store.get('task', q['question_id'])
        self.assertEqual(result['delivery_status'], 'queued')
        self.assertIn('上下文', result['delivery_error'])
        self.assertEqual(len(self.calls), 0)
        self.accept(self.event())
        self.channel.deliver_one()
        self.assertEqual(self.store.get('task', q['question_id'])['delivery_status'], 'sent')
        self.assertIsNone(self.store.get('task', q['question_id'])['reply'])
        _, body, headers = self.calls[0]
        self.assertEqual(body['msg']['context_token'], 'context_one')
        self.assertEqual(body['msg']['to_user_id'], 'test_user')
        self.assertEqual(body['msg']['client_id'], q['question_id'])
        self.assertEqual(headers['Authorization'], 'Bearer fake_weixin_token')
        uin = next(v for k, v in headers.items() if k.lower() == 'x-wechat-uin')
        self.assertTrue(base64.b64decode(uin).decode().isdigit())

    def test_poll_reply_and_persistent_cursor_context(self):
        q = self.question()
        self.messages = [self.event(f"/reply {q['question_id']} 同意")]
        self.channel.poll_once()
        result = self.store.get('task', q['question_id'])
        self.assertEqual(result['reply']['text'], '同意')
        self.assertEqual(result['reply']['source'], 'weixin')
        self.assertEqual(result['reply']['sender_id'], 'test_user')
        self.assertFalse(result['is_simulated'])
        reopened = Store(self.store.path)
        self.assertEqual(reopened.state_get('weixin_cursor'), 'cursor_next')
        self.assertEqual(reopened.state_get('weixin_context'), 'context_one')
        self.channel.poll_once()
        self.assertEqual(self.calls[-1][1]['get_updates_buf'], 'cursor_next')

    def test_duplicate_cannot_overwrite_reply_or_newer_context(self):
        q = self.question()
        old = self.event(f"/reply {q['question_id']} 第一答")
        self.accept(old)
        self.accept(self.event(mid=2, context='context_new'))
        old['item_list'][0]['text_item']['text'] = f"/reply {q['question_id']} 重复篡改"
        self.accept(old)
        self.assertEqual(self.store.get('task', q['question_id'])['reply']['text'], '第一答')
        self.assertEqual(self.store.state_get('weixin_context'), 'context_new')

    def test_stranger_group_other_bot_partial_and_other_channel_ignored(self):
        q = self.question()
        other = self.question('feishu', 'other')
        for i, extra in enumerate([{'user': 'stranger'}, {'group_id': 'group'}, {'to_user_id': 'other_bot'},
                                    {'message_type': 2}, {'message_state': 1}]):
            self.accept(self.event(f"/reply {q['question_id']} 错答", mid=i + 10, **extra))
        self.assertIsNone(self.store.get('task', q['question_id'])['reply'])
        self.assertEqual(self.store.state_get('weixin_context'), '')
        self.accept(self.event(f"/reply {other['question_id']} 错渠道"))
        self.assertIsNone(self.store.get('task', other['question_id'])['reply'])

    def test_business_error_and_unknown_never_blindly_resend(self):
        self.accept(self.event())
        q = self.question()
        self.send_result = {'ret': -3, 'errmsg': 'fake_weixin_token should not leak'}
        self.channel.deliver_one()
        self.channel.deliver_one()
        result = self.store.get('task', q['question_id'])
        self.assertEqual(result['delivery_status'], 'failed')
        self.assertNotIn('fake_weixin_token', result['delivery_error'])
        self.assertEqual(len(self.calls), 1)
        other = self.question(request='network')
        self.http_status = 500
        self.channel.deliver_one()
        self.channel.deliver_one()
        self.assertEqual(self.store.get('task', other['question_id'])['delivery_status'], 'unknown')
        self.assertEqual(len(self.calls), 2)

    def test_expired_context_waits_for_fresh_inbound_before_retry(self):
        self.accept(self.event())
        q = self.question()
        self.send_result = {'ret': -2}
        self.channel.deliver_one()
        self.channel.deliver_one()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.store.get('task', q['question_id'])['delivery_status'], 'queued')
        self.accept(self.event(mid=2, context='fresh'))
        self.send_result = {'ret': 0}
        self.channel.deliver_one()
        self.assertEqual(self.calls[-1][1]['msg']['context_token'], 'fresh')
        self.assertEqual(self.store.get('task', q['question_id'])['delivery_status'], 'sent')

    def test_expired_login_stops_delivery_and_new_questions_remain_queued(self):
        self.accept(self.event())
        q = self.question()
        self.send_result = {'ret': -14}
        self.channel.deliver_one()
        self.assertTrue(self.channel.stale)
        self.assertEqual(self.store.get('task', q['question_id'])['delivery_status'], 'failed')
        other = self.question(request='other')
        self.channel.deliver_one()
        self.assertEqual(len(self.calls), 1)
        self.assertIn('扫码', self.store.get('task', other['question_id'])['delivery_error'])
        self.poll_result = {'ret': 0, 'errcode': -14}
        with self.assertRaises(WeixinError):
            self.channel.poll_once()

    def test_lease_recovery_and_account_binding(self):
        q = self.question()
        self.store.claim_outgoing('weixin')
        self.channel.start()
        self.assertEqual(self.store.get('task', q['question_id'])['delivery_status'], 'unknown')
        lease = json.loads(self.store.state_get('weixin_lease'))
        self.assertGreater(lease['until'] - time.time(), 80)
        with self.assertRaises(ValueError):
            WeixinChannel(self.store, self.api).start()
        with self.assertRaises(ValueError):
            WeixinChannel(self.store, WeixinApi({**self.account, 'ilink_user_id': 'other'}))

    def test_persistence_failure_does_not_advance_cursor(self):
        q = self.question()
        response = {'msgs': [self.event(f"/reply {q['question_id']} 恢复")], 'get_updates_buf': 'next'}
        with patch.object(self.store, 'external_events', side_effect=OSError('fake disk error')):
            with self.assertRaises(OSError):
                self.channel.accept(response)
        self.assertEqual(self.store.state_get('weixin_cursor'), '')
        self.assertIsNone(self.store.get('task', q['question_id'])['reply'])
        self.channel.accept(response)
        self.assertEqual(self.store.get('task', q['question_id'])['reply']['text'], '恢复')

    def test_malformed_batch_and_missing_success_do_not_fake_delivery(self):
        with self.assertRaises(ChannelError):
            self.channel.accept({'msgs': {}, 'get_updates_buf': 'skip'})
        self.assertEqual(self.store.state_get('weixin_cursor'), '')
        self.accept(self.event())
        q = self.question()
        self.send_result = {}
        self.channel.deliver_one()
        self.assertEqual(self.store.get('task', q['question_id'])['delivery_status'], 'unknown')

    def test_login_saves_credentials_and_uses_distinct_auth_headers(self):
        path, shown = Path(self.temp.name) / 'account.json', []
        account = login(path, shown.append, lambda _: '', base=self.base)
        self.assertEqual(load_account(path), account)
        self.assertEqual(shown, ['https://example.invalid/fake-login'])
        self.assertNotIn('Authorization', self.calls[0][2])
        self.assertIn('Authorizationtype', {k.title(): v for k, v in self.calls[0][2].items()})
        self.assertFalse(any(k.lower() in ('authorization', 'authorizationtype', 'x-wechat-uin')
                             for k in self.calls[1][2]))

    def test_failed_login_preserves_credentials_and_untrusted_host_rejected(self):
        path = Path(self.temp.name) / 'account.json'
        save_account(path, self.account)
        self.qr_status = 'expired'
        with self.assertRaises(ValueError):
            login(path, lambda _: None, lambda _: '', base=self.base)
        self.assertEqual(load_account(path), self.account)
        for bad in ['https://evil.example', 'https://ilinkai.weixin.qq.com.evil.test',
                    'https://user:pass@ilinkai.weixin.qq.com', 'https://ilinkai.weixin.qq.com/path']:
            with self.assertRaises(ValueError):
                api_base(bad)

    def test_login_verification_redirect_and_blocked_state(self):
        path, entered = Path(self.temp.name) / 'account.json', []
        responses = [{'qrcode': 'fake_qr', 'qrcode_img_content': 'fake_content'},
                     {'status': 'scaned_but_redirect', 'redirect_host': 'ilinkai2.weixin.qq.com'},
                     {'status': 'need_verifycode'}, {'status': 'scaned'},
                     {**self.account, 'status': 'confirmed'}]
        with patch('codex_user_bridge.weixin_login.json_request', side_effect=responses) as request, \
                patch('codex_user_bridge.weixin_login.time.sleep'):
            login(path, lambda _: None, lambda prompt: entered.append(prompt) or '123456')
        self.assertEqual(len(entered), 1)
        self.assertIn('ilinkai2.weixin.qq.com', request.call_args_list[2].args[0])
        self.assertIn('verify_code=123456', request.call_args_list[3].args[0])
        self.assertNotIn('verify_code=', request.call_args_list[4].args[0])
        original = path.read_text()
        with patch('codex_user_bridge.weixin_login.json_request', side_effect=[responses[0], {'status': 'binded_redirect'}]):
            with self.assertRaises(ValueError):
                login(path, lambda _: None, lambda _: '')
        self.assertEqual(path.read_text(), original)

    def test_mcp_end_to_end(self):
        server = make_server(self.store, 0, channel='weixin')
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        async def run():
            params = StdioServerParameters(command=sys.executable, args=['-m', 'codex_user_bridge.mcp_server'],
                env={'BRIDGE_BASE_URL': f'http://127.0.0.1:{server.server_port}'})
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    result = await client.call_tool('bridge_send_question', {'task_id': 'mcp', 'request_id': 'r', 'message': '微信问题'})
                    self.assertFalse(result.isError)
                    q = result.structuredContent
                    self.accept(self.event())
                    self.channel.deliver_one()
                    self.messages = [self.event(f"/reply {q['question_id']} 微信回复", mid=2)]
                    self.channel.poll_once()
                    result = await client.call_tool('bridge_get_reply', {'task_id': 'mcp', 'question_id': q['question_id']})
                    self.assertFalse(result.isError)
                    self.assertEqual(result.structuredContent['reply']['source'], 'weixin')
        try:
            asyncio.run(run())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
