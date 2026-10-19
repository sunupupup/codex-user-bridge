"""Telegram 文本私聊适配：后台发送、长轮询接收，不启动模型。"""
import json
import logging
import os
import re
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class TelegramError(RuntimeError):
    def __init__(self, code=None, retry_after=0, uncertain=False):
        self.code = code
        self.retry_after = retry_after
        self.uncertain = uncertain
        # urllib 异常包含带 Token 的 URL；禁止透传原异常或服务端描述。
        message = ('问题含选项超过 Telegram 长度限制，请缩短问题后使用新 request_id。' if code == 413
                   else f'Telegram API 拒绝请求（代码 {code}），请检查 Bot、接收者及 webhook 配置。')
        super().__init__('Telegram 请求结果未知，请检查网络；不会自动重发。' if uncertain else message)


class TelegramApi:
    def __init__(self, token, base_url='https://api.telegram.org'):
        if not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]{8,}', token or ''):
            raise ValueError('TELEGRAM_BOT_TOKEN 缺失或格式错误。')
        parsed = urlsplit(base_url)
        if base_url != 'https://api.telegram.org' and not (
                parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost')
                and not parsed.username and not parsed.password and parsed.path in ('', '/')):
            raise ValueError('API 地址仅允许 Telegram 官方地址或本地测试服务。')
        self._token = token
        self._base = base_url.rstrip('/')
        self.bot_id = token.split(':', 1)[0]

    def call(self, method, body=None):
        request = Request(f'{self._base}/bot{self._token}/{method}',
                          data=json.dumps(body or {}).encode('utf-8'),
                          headers={'Content-Type': 'application/json'})
        try:
            try:
                response = urlopen(request, timeout=12)
            except HTTPError as exc:
                response = exc
            with response:
                result = json.load(response)
                if not isinstance(result, dict):
                    raise ValueError('invalid response')
                if result.get('ok') is not True:
                    code = result.get('error_code', response.status)
                    retry = result.get('parameters', {}).get('retry_after', 0)
                    raise TelegramError(code, max(0, float(retry)), uncertain=int(code) >= 500)
                if 'result' not in result:
                    raise ValueError('missing result')
                return result['result']
        except TelegramError:
            raise
        except (URLError, OSError, ValueError, TypeError):
            raise TelegramError(uncertain=True) from None

    def validate(self):
        me = self.call('getMe')
        if str(me.get('id')) != self.bot_id or me.get('is_bot') is not True:
            raise ValueError('getMe 返回的机器人身份不匹配。')
        if self.call('getWebhookInfo').get('url'):
            raise ValueError('此 Bot 已设置 webhook，不能同时长轮询；请使用专用 Bot 或自行迁移。')

    def send(self, chat_id, question):
        options = '\n'.join(f'{i + 1}. {value}' for i, value in enumerate(question['options']))
        content = (f"Codex 问题 {question['question_id']}\n"
                   '请引用这条消息回复，或发送 /reply 问题编号 回答。\n'
                   f"任务：{question['task_id']}\n\n{question['message']}\n{options}")
        # 不截断确认问题，避免用户只看到部分操作或金额；超长时明确失败。
        if len(content.encode('utf-16-le')) // 2 > 4096:
            raise TelegramError(413)
        result = self.call('sendMessage', {'chat_id': chat_id, 'text': content,
                                          'reply_markup': {'force_reply': True, 'selective': True}})
        message_id = result.get('message_id')
        if not isinstance(message_id, int) or message_id <= 0 or str(result.get('chat', {}).get('id')) != str(chat_id):
            raise TelegramError(uncertain=True)
        return message_id

    def updates(self, offset, timeout=2):
        result = self.call('getUpdates', {'offset': offset, 'timeout': timeout,
                                          'allowed_updates': ['message']})
        if not isinstance(result, list):
            raise TelegramError(uncertain=True)
        return result


class TelegramChannel:
    def __init__(self, store, api, chat_id, user_id):
        if int(chat_id) <= 0 or int(user_id) <= 0 or int(chat_id) != int(user_id):
            raise ValueError('第一版仅支持个人私聊，TELEGRAM_CHAT_ID 和 TELEGRAM_USER_ID 应为相同正整数。')
        self.store, self.api = store, api
        self.chat_id, self.user_id = int(chat_id), int(user_id)
        self.owner = uuid.uuid4().hex
        store.bind_telegram(f'{api.bot_id}:{self.chat_id}:{self.user_id}')

    @classmethod
    def from_env(cls, store):
        try:
            return cls(store, TelegramApi(os.environ.get('TELEGRAM_BOT_TOKEN', '')),
                       int(os.environ.get('TELEGRAM_CHAT_ID', '')),
                       int(os.environ.get('TELEGRAM_USER_ID') or os.environ.get('TELEGRAM_CHAT_ID', '')))
        except (TypeError, ValueError):
            raise ValueError('请配置有效的 TELEGRAM_BOT_TOKEN、正整数 TELEGRAM_CHAT_ID；'
                             '仅支持私聊，USER_ID 默认等于 CHAT_ID。') from None

    def start(self):
        if not self.store.lease(self.owner):
            raise ValueError('此数据库已有 Telegram 接收进程运行，请勿重复启动。')
        try:
            self.api.validate()
            self.store.recover_sending()
        except Exception:
            self.store.lease(self.owner, release=True)
            raise

    def deliver_one(self):
        question = self.store.claim_outgoing()
        if question is None:
            return 0
        try:
            message_id = self.api.send(self.chat_id, question)
            self.store.delivered(question['question_id'], 'sent', message_id)
        except TelegramError as exc:
            state = 'unknown' if exc.uncertain else ('queued' if exc.code == 429 else 'failed')
            self.store.delivered(question['question_id'], state, error=str(exc),
                                 retry_after=max(1, exc.retry_after) if exc.code == 429 else 0)
            logging.warning('%s', exc)
            return max(1, exc.retry_after) if exc.code == 429 else 0
        return 0

    def receive(self):
        updates = self.api.updates(self.store.offset())
        for update in sorted(updates, key=lambda item: item.get('update_id', -1)):
            update_id = update.get('update_id')
            if not isinstance(update_id, int):
                continue
            message = update.get('message', {})
            chat, sender = message.get('chat', {}), message.get('from', {})
            answer = message.get('text')
            reply = None
            # 同时校验会话、真人身份和私聊类型；群聊、其他人、无关联文本不写入回答。
            if (chat.get('id') == self.chat_id and chat.get('type') == 'private'
                    and sender.get('id') == self.user_id and not sender.get('is_bot')
                    and isinstance(answer, str) and answer.strip() and len(answer) <= 4000):
                match = re.fullmatch(r'/reply\s+(q_[0-9a-f]{32})\s+(.+)', answer.strip(), re.DOTALL)
                message_id = message.get('reply_to_message', {}).get('message_id')
                if match:
                    reply = (match[1], None, match[2].strip(), self.user_id)
                elif isinstance(message_id, int) and not answer.startswith('/'):
                    reply = (None, message_id, answer.strip(), self.user_id)
            self.store.accept_update(update_id, reply)

    def run(self, stop):
        delay = 1
        try:
            while not stop.is_set():
                if not self.store.lease(self.owner):
                    logging.error('Telegram 进程租约失效，停止收发。')
                    return
                try:
                    wait = self.deliver_one()
                    if wait:
                        stop.wait(min(wait, 30))
                        continue
                    self.receive()
                    delay = 1
                except TelegramError as exc:
                    logging.warning('%s', exc)
                    stop.wait(min(max(delay, exc.retry_after), 30))
                    delay = min(delay * 2, 30)
                except Exception:
                    # 不输出异常链，避免第三方异常或 URL 中的 Token 进入日志。
                    logging.error('Telegram 后台处理失败，将退避后重试；请检查配置与数据库。')
                    stop.wait(3)
        finally:
            self.store.lease(self.owner, release=True)
