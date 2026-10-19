"""工具只依赖统一接口；HTTP 路径和错误转换留在本地 Adapter。"""

import asyncio
import json
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import ProxyHandler, Request, build_opener


class ChannelAdapter(Protocol):
    async def send(self, task_id: str, request_id: str, message: str, options: list[str]) -> dict: ...
    async def get_reply(self, task_id: str, question_id: str) -> dict: ...


class LocalHttpAdapter:
    def __init__(self, base_url='http://127.0.0.1:5555'):
        parsed = urlsplit(base_url)
        if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost')
                or parsed.path not in ('', '/') or parsed.query or parsed.fragment
                or parsed.username or parsed.password):
            raise ValueError('第一版只支持 http://127.0.0.1:端口 或 localhost。')
        self.base_url = base_url.rstrip('/')

    def _request(self, path, payload=None):
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode('utf-8')
        request = Request(self.base_url + path, data=body,
                          headers={'Content-Type': 'application/json'})
        try:
            # 本地消息不经过系统代理，避免代理设置改变本机请求的路由。
            with build_opener(ProxyHandler({})).open(request, timeout=5) as response:
                return json.load(response)
        except HTTPError as exc:
            detail = json.loads(exc.read()).get('error', str(exc))
            raise RuntimeError(f'本地服务 HTTP {exc.code}: {detail}') from exc
        except (URLError, TimeoutError, OSError) as exc:
            # 发送失败可能已被服务接受；提示复用 request_id，而非悄悄重发。
            raise RuntimeError('无法取得本地服务结果。请检查服务是否启动；'
                               '发送结果可能未知，重试时沿用原 request_id。') from exc

    async def send(self, task_id, request_id, message, options):
        return await asyncio.to_thread(self._request, '/chat', {
            'task_id': task_id, 'request_id': request_id, 'message': message, 'options': options})

    async def get_reply(self, task_id, question_id):
        path = '/chat/' + quote(question_id, safe='') + '?' + urlencode({'task_id': task_id})
        return await asyncio.to_thread(self._request, path)
