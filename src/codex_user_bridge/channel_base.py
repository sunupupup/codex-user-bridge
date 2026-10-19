"""外部渠道共享出站队列；协议收发仍由各 Adapter 实现。"""
import json
import logging
import re
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class ChannelError(RuntimeError):
    def __init__(self, message='渠道请求失败，请检查凭据与网络。', uncertain=False, retry_after=0):
        super().__init__(message)
        self.uncertain, self.retry_after = uncertain, retry_after


def json_request(url, body=None, headers=None, timeout=12):
    """统一隐藏网络异常链；不能把含凭据的 URL、正文或平台描述写入日志。"""
    request = Request(url, data=None if body is None else json.dumps(body).encode(),
                      headers={'Content-Type': 'application/json', **(headers or {})})
    try:
        try:
            response = urlopen(request, timeout=timeout)
        except HTTPError as exc:
            response = exc
        with response:
            if response.status == 429:
                retry = response.headers.get('Retry-After', '30')
                raise ChannelError('渠道限流，稍后自动重试。', retry_after=int(retry) if retry.isdigit() else 30)
            if response.status >= 500:
                raise ChannelError('平台服务异常，发送结果未知。', uncertain=True)
            if response.status >= 400:
                raise ChannelError(f'渠道 HTTP {response.status}，请检查凭据、权限与配置。')
            value = json.load(response)
            if not isinstance(value, dict):
                raise ValueError('invalid JSON')
            return value
    except ChannelError:
        raise
    except (ValueError, OSError, URLError):
        raise ChannelError('渠道网络或响应异常，结果未知。', uncertain=True) from None


def check_base(url, official):
    parsed = urlsplit(url)
    if url not in official and not (parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost')
                                   and parsed.path in ('', '/') and not parsed.username and not parsed.password):
        raise ValueError('不支持此渠道 API 地址。')
    return url.rstrip('/')


def question_text(question):
    options = '\n'.join(f'{i + 1}. {v}' for i, v in enumerate(question['options']))
    return (f"Codex 问题 {question['question_id']}\n"
            f"回复：/reply {question['question_id']} 回答内容\n"
            f"任务：{question['task_id']}\n\n{question['message']}\n{options}")


def parse_reply(text):
    if not isinstance(text, str):
        return None, None
    match = re.fullmatch(r'/reply\s+(q_[0-9a-f]{32})\s+(.+)', text.strip(), re.DOTALL)
    return (match[1], match[2].strip()) if match and len(match[2]) <= 4000 else (None, None)


class ExternalChannel:
    channel = ''
    lease_seconds = 45

    def __init__(self, store, api, identity):
        self.store, self.api = store, api
        self.owner = uuid.uuid4().hex
        store.bind_channel(self.channel, identity)

    def start(self):
        if not self.store.lease(self.owner, channel=self.channel, ttl=self.lease_seconds):
            raise ValueError('此数据库已有该渠道接收进程，请勿重复启动。')
        try:
            self.api.validate()
            self.store.recover_sending(self.channel)
        except Exception:
            self.close()
            raise

    def close(self):
        self.store.lease(self.owner, release=True, channel=self.channel)

    def deliver_one(self):
        question = self.store.claim_outgoing(self.channel)
        if question is None:
            return
        try:
            message_id = self.send(question)
            self.store.external_delivery(self.channel, question['question_id'], 'sent', message_id)
        except ChannelError as exc:
            state = 'unknown' if exc.uncertain else ('queued' if exc.retry_after else 'failed')
            self.store.external_delivery(self.channel, question['question_id'], state,
                                         error=str(exc), retry_after=exc.retry_after)
            logging.warning('%s: %s', self.channel, exc)
