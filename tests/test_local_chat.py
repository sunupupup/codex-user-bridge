"""手动聊天渠道的隔离、并发回复与 HTTP 同源边界。"""
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from codex_user_bridge.store import Store
from codex_user_bridge.service import make_server
from codex_user_bridge.mcp_server import Question


class LocalChatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'chat.sqlite3')
        self.server = make_server(self.store, 0, 'local_chat')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def call(self, path, body=None, origin=None):
        headers = {'Content-Type': 'application/json'}
        if origin:
            headers['Origin'] = origin
        request = Request(self.base + path, data=None if body is None else json.dumps(body).encode(), headers=headers)
        with build_opener(ProxyHandler({})).open(request, timeout=3) as result:
            return json.load(result)

    def send(self, request='one'):
        return self.call('/chat', {'task_id': 'test', 'request_id': request, 'message': '继续吗？', 'options': ['继续', '稍后']})

    def test_manual_reply_persisted_and_other_question_untouched(self):
        first, second = self.send(), self.send('two')
        self.store.auto_reply(0, '不应该自动回答')
        self.assertEqual(self.call('/ui/questions')['questions'][0]['status'], 'pending')
        body = {'task_id': 'test', 'question_id': first['question_id'], 'text': '可以'}
        answer = self.call('/ui/reply', body, self.base)
        parsed = Question.model_validate(answer)
        self.assertEqual(parsed.reply.source, 'local_chat')
        self.assertFalse(parsed.is_simulated)
        self.assertEqual(parsed.delivery_status, 'sent')
        self.assertIsNone(self.store.get('test', second['question_id'])['reply'])
        self.assertEqual(Store(self.store.path).get('test', first['question_id'])['reply']['text'], '可以')
        self.assertEqual(self.call('/ui/reply', body, self.base)['reply'], answer['reply'])
        with self.assertRaises(HTTPError) as error:
            self.call('/ui/reply', {**body, 'text': '覆盖'}, self.base)
        self.assertEqual(error.exception.code, 409)

    def test_wrong_task_other_channel_and_cross_origin_rejected(self):
        q = self.send()
        body = {'task_id': 'test', 'question_id': q['question_id'], 'text': '回答'}
        for origin in ('https://evil.example', 'null', 'http://localhost:1'):
            with self.assertRaises(HTTPError) as error:
                self.call('/ui/reply', body, origin)
            self.assertEqual(error.exception.code, 403)
        with self.assertRaises(HTTPError) as error:
            self.call('/ui/reply', {**body, 'task_id': 'wrong'}, self.base)
        self.assertEqual(error.exception.code, 404)
        other = self.store.send('test', 'external', '其他渠道', [], 'telegram')
        self.assertEqual(len(self.call('/ui/questions')['questions']), 1)
        with self.assertRaises(HTTPError):
            self.call('/ui/reply', {**body, 'question_id': other['question_id']}, self.base)

    def test_concurrent_answers_keep_first(self):
        q = self.send()
        def answer(text):
            try:
                self.store.local_reply('test', q['question_id'], text)
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(answer, ['甲', '乙']))
        self.assertEqual(sum(results), 1)

    def test_page_resource_and_invalid_answer(self):
        with build_opener(ProxyHandler({})).open(self.base) as response:
            self.assertIn('text/html', response.headers['Content-Type'])
            self.assertEqual(response.headers['X-Frame-Options'], 'DENY')
            self.assertIn('textContent', response.read().decode())
        q = self.send()
        for answer in (' ', 'a' * 4001):
            with self.assertRaises(HTTPError) as error:
                self.call('/ui/reply', {'task_id': 'test', 'question_id': q['question_id'], 'text': answer}, self.base)
            self.assertEqual(error.exception.code, 400)
