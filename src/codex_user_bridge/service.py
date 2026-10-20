"""仅监听本机的问答服务：浏览器手动聊天或外部渠道。"""

import argparse
import json
import logging
import math
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .store import Store

DEFAULT_REPLY = '【本地模拟回复】消息已收到；请继续不依赖用户决定的工作。这不是用户授权。'


def text(value, name, maximum):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f'{name} 必须是 1～{maximum} 字符的非空文本。')
    return value.strip()


def make_server(store: Store, port=5555, channel='local_mock'):
    class Handler(BaseHTTPRequestHandler):
        def discard_body(self):
            # 拒绝 POST 前有限地读掉请求体，避免 Windows 关闭连接时以 RST 吞掉错误响应。
            try:
                size = int(self.headers.get('Content-Length', '0'))
                self.connection.settimeout(1)
                if 0 < size <= 32768:
                    self.rfile.read(size)
            except (ValueError, OSError):
                pass

        def respond(self, status, payload):
            data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)

        def allowed(self):
            # 页面仅允许精确同源请求；不能因添加 UI 就允许任意网站写入回答。
            hosts = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            host, origin = self.headers.get('Host'), self.headers.get('Origin')
            if (host not in hosts or (origin is not None and not
                    (channel == 'local_chat' and origin == 'http://' + host))
                    or self.headers.get('Sec-Fetch-Site') == 'cross-site'):
                if self.command == 'POST':
                    self.discard_body()
                self.respond(403, {'error': '只接受本机客户端请求。'})
                return False
            return True

        def do_GET(self):
            if not self.allowed():
                return
            route = urlsplit(self.path)
            if route.path == '/' and channel == 'local_chat':
                data = files('codex_user_bridge').joinpath('chat.html').read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Frame-Options', 'DENY')
                self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
                self.end_headers()
                self.wfile.write(data)
            elif route.path == '/ui/questions' and channel == 'local_chat':
                self.respond(200, {'questions': store.local_questions()})
            elif route.path == '/health':
                self.respond(200, {'service': 'codex-user-bridge', 'channel': channel,
                                   'is_simulated': channel == 'local_mock'})
            elif route.path.startswith('/chat/'):
                task_id = parse_qs(route.query).get('task_id', [''])[0]
                result = store.get(task_id, route.path[len('/chat/'):])
                self.respond(200 if result else 404, result or {'error': '问题不存在或 task_id 不匹配。'})
            else:
                self.respond(404, {'error': '接口不存在。'})

        def do_POST(self):
            if not self.allowed():
                return
            ui_reply = self.path == '/ui/reply' and channel == 'local_chat'
            if self.path != '/chat' and not ui_reply:
                self.discard_body()
                self.respond(404, {'error': '接口不存在。'})
                return
            if self.headers.get_content_type() != 'application/json':
                self.discard_body()
                self.respond(415, {'error': '请使用 application/json。'})
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 32768:
                    raise ValueError('请求体必须在 1～32768 字节内。')
                self.connection.settimeout(5)
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError('请求体必须是 JSON 对象。')
                task_id = text(data.get('task_id'), 'task_id', 200)
                if ui_reply:
                    question_id = text(data.get('question_id'), 'question_id', 200)
                    answer = text(data.get('text'), 'text', 4000)
                    try:
                        result = store.local_reply(task_id, question_id, answer)
                    except LookupError as exc:
                        self.respond(404, {'error': str(exc)})
                        return
                    except ValueError as exc:
                        self.respond(409, {'error': str(exc)})
                        return
                    self.respond(200, result)
                    return
                request_id = text(data.get('request_id'), 'request_id', 200)
                message = text(data.get('message'), 'message', 4000)
                options = data.get('options', [])
                if not isinstance(options, list) or len(options) > 10:
                    raise ValueError('options 必须是最多 10 项的列表。')
                options = [text(item, 'option', 200) for item in options]
            except (ValueError, OSError) as exc:
                self.respond(400, {'error': str(exc)})
                return
            try:
                result = store.send(task_id, request_id, message, options, channel)
            except ValueError as exc:
                self.respond(409, {'error': str(exc)})
                return
            self.respond(200, result)

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    return server


def reply_worker(store, stop, interval=3.0, delay=3.0):
    while not stop.wait(interval):
        try:
            count = store.auto_reply(delay, DEFAULT_REPLY)
            if count:
                logging.info('生成 %s 条模拟回复', count)
        except Exception:
            # 单次扫描失败保留 pending，下次扫描可恢复；错误必须可见。
            logging.exception('模拟回复扫描失败')


def positive(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError('必须是有限正数。')
    return number


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=5555)
    parser.add_argument('--db', type=Path, default=Path('.local/messages.sqlite3'))
    parser.add_argument('--interval', type=positive, default=3.0)
    parser.add_argument('--delay', type=positive, default=3.0)
    parser.add_argument('--channel', choices=['local_chat', 'local_mock', 'telegram', 'feishu', 'weixin'], default='local_chat')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    store = Store(args.db)
    server = make_server(store, args.port, args.channel)
    stop = threading.Event()
    worker = None
    if args.channel not in ('local_mock', 'local_chat'):
        try:
            if args.channel == 'telegram':
                from .telegram import TelegramChannel
                external = TelegramChannel.from_env(store)
            elif args.channel == 'feishu':
                from .feishu import FeishuChannel
                external = FeishuChannel.from_env(store)
            else:
                from .weixin import WeixinChannel
                external = WeixinChannel.from_env(store)
            external.start()
        except (ValueError, RuntimeError) as exc:
            server.server_close()
            parser.exit(2, f'{exc}\n')
        worker = threading.Thread(target=external.run, args=(stop,), daemon=True)
    elif args.channel == 'local_mock':
        worker = threading.Thread(target=reply_worker,
                                  args=(store, stop, args.interval, args.delay), daemon=True)
    if worker:
        worker.start()
    logging.info('渠道 %s，本地服务 http://127.0.0.1:%s，数据库 %s',
                 args.channel, server.server_port, args.db.resolve())
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        if worker:
            worker.join(timeout=45 if args.channel == 'weixin' else 15)
        server.server_close()


if __name__ == '__main__':
    main()
