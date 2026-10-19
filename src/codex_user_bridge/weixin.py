"""个人微信 ClawBot/iLink 文本通道；不依赖另一个 Agent 或公网回调。"""
import base64
import json
import logging
import os
from pathlib import Path
import secrets
from urllib.parse import urlsplit

from .channel_base import ChannelError, ExternalChannel, json_request, parse_reply, question_text

DEFAULT_BASE = 'https://ilinkai.weixin.qq.com'
VERSION = '0.5.0'


def api_base(value):
    """仅信任微信 HTTPS 地址；HTTP 回环地址只用于协议测试。"""
    if not isinstance(value, str):
        raise ValueError('微信 API 地址无效。')
    parsed = urlsplit(value)
    official = (parsed.scheme == 'https' and parsed.hostname and
                parsed.hostname.endswith('.weixin.qq.com')
                and parsed.port in (None, 443))
    local = parsed.scheme == 'http' and parsed.hostname in ('localhost', '127.0.0.1')
    if not (official or local) or parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment:
        raise ValueError('微信返回了不受支持的 API 地址。')
    return value.rstrip('/')


def app_headers():
    major, minor, patch = map(int, VERSION.split('.'))
    return {'iLink-App-Id': 'bot', 'iLink-App-ClientVersion': str((major << 16) | (minor << 8) | patch)}


def post_headers(token=''):
    headers = {**app_headers(), 'AuthorizationType': 'ilink_bot_token',
               'X-WECHAT-UIN': base64.b64encode(str(secrets.randbits(32)).encode()).decode()}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    return headers


def credentials_path():
    return Path(os.environ.get('WEIXIN_CREDENTIALS', '.local/weixin-account.json'))


def load_account(path):
    try:
        account = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise ValueError('无法读取微信凭据，请先运行 scripts/weixin_setup.py 扫码登录。') from None
    return validate_account(account)


def validate_account(account):
    if not isinstance(account, dict) or any(not isinstance(account.get(key), str) or not account[key].strip()
            for key in ('bot_token', 'ilink_bot_id', 'ilink_user_id', 'baseurl')):
        raise ValueError('微信凭据不完整，请重新扫码。')
    return {key: api_base(account[key]) if key == 'baseurl' else account[key]
            for key in ('bot_token', 'ilink_bot_id', 'ilink_user_id', 'baseurl')}


def save_account(path, account):
    account = validate_account(account)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 同目录临时文件 + 原子替换；未完成的扫码不会破坏上次可用的账号。
    temporary = path.with_name(path.name + '.' + secrets.token_hex(6) + '.tmp')
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(account, stream)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class WeixinError(ChannelError):
    def __init__(self, code):
        self.code = code
        message = ('微信登录已失效，请停止服务、重新扫码并重启。' if code == -14 else
                   '微信拒绝发送（-2），可能与会话上下文或平台策略有关；等待新消息后再试。' if code == -2 else
                   '微信接口拒绝请求，请检查账号状态。')
        super().__init__(message)


class WeixinApi:
    def __init__(self, account):
        self.account = validate_account(account)
        self.base = self.account['baseurl']

    def validate(self):
        # 扫码已绑定账号，启动不额外消费消息；远端有效性由首个长轮询确认。
        validate_account(self.account)

    def request(self, endpoint, body, timeout=12):
        value = json_request(self.base + '/ilink/bot/' + endpoint,
            {**body, 'base_info': {'channel_version': VERSION, 'bot_agent': 'CodexUserBridge/' + VERSION}},
            post_headers(self.account['bot_token']), timeout=timeout)
        codes = [value.get('ret', 0), value.get('errcode', 0)]
        if -14 in codes:
            raise WeixinError(-14)
        if any(code != 0 for code in codes):
            raise WeixinError(next(code for code in codes if code != 0))
        if value.get('ret') != 0:
            raise ChannelError('微信未明确确认请求结果，不自动重发。', uncertain=True)
        return value

    def updates(self, cursor):
        return self.request('getupdates', {'get_updates_buf': cursor}, timeout=40)

    def send(self, question, context):
        self.request('sendmessage', {'msg': {
            'from_user_id': '', 'to_user_id': self.account['ilink_user_id'],
            'client_id': question['question_id'], 'message_type': 2, 'message_state': 2,
            'context_token': context,
            'item_list': [{'type': 1, 'text_item': {'text': question_text(question)}}],
        }})
        return question['question_id']


class WeixinChannel(ExternalChannel):
    channel = 'weixin'
    # 长轮询最长 40 秒，加上发送与落盘时间；旧消费者不能在正常轮询中失去租约。
    lease_seconds = 90

    def __init__(self, store, api):
        self.account, self.stale = api.account, False
        super().__init__(store, api, self.account['ilink_bot_id'] + ':' + self.account['ilink_user_id'])

    @classmethod
    def from_env(cls, store):
        return cls(store, WeixinApi(load_account(credentials_path())))

    def deliver_one(self):
        question = self.store.claim_outgoing(self.channel)
        if question is None:
            return
        context = self.store.state_get('weixin_context')
        if self.stale or not context:
            error = '微信登录已失效，请重新扫码并重启。' if self.stale else '等待微信会话上下文，请先给机器人发一条消息。'
            self.store.external_delivery(self.channel, question['question_id'], 'queued', error=error)
            return
        try:
            message_id = self.api.send(question, context)
            self.store.external_delivery(self.channel, question['question_id'], 'sent', message_id)
        except ChannelError as exc:
            if isinstance(exc, WeixinError) and exc.code in (-14, -2):
                self.stale = exc.code == -14
                self.store.external_events(self.channel, [], {'weixin_context': ''})
                state = 'failed' if self.stale else 'queued'
            else:
                state = 'unknown' if exc.uncertain else ('queued' if exc.retry_after else 'failed')
            self.store.external_delivery(self.channel, question['question_id'], state,
                                         error=str(exc), retry_after=exc.retry_after)
            logging.warning('weixin: %s', exc)

    def accept(self, response):
        events, states = [], {}
        cursor = response.get('get_updates_buf')
        if isinstance(cursor, str) and cursor:
            states['weixin_cursor'] = cursor
        messages = response.get('msgs', [])
        if not isinstance(messages, list):
            raise ChannelError('微信消息批次格式无效，保留游标等待重收。')
        for message in messages:
            if (not isinstance(message, dict) or message.get('from_user_id') != self.account['ilink_user_id']
                    or message.get('message_type') != 1 or message.get('group_id')
                    or message.get('to_user_id') not in (None, '', self.account['ilink_bot_id'])
                    or message.get('message_state', 2) != 2):
                continue
            mid = message.get('message_id')
            if isinstance(mid, bool) or not isinstance(mid, (str, int)) or str(mid) == '':
                continue
            # 只有绑定用户的完整消息能更新上下文；普通“你好”可用于激活会话，不会猜成某题回答。
            context = message.get('context_token')
            event_states = {'weixin_context': context} if isinstance(context, str) and context else {}
            items = message.get('item_list', [])
            if not isinstance(items, list):
                continue
            texts = [item.get('text_item', {}).get('text') for item in items
                     if isinstance(item, dict) and item.get('type') == 1 and isinstance(item.get('text_item'), dict)]
            text = '\n'.join(value for value in texts if isinstance(value, str))
            qid, answer = parse_reply(text)
            events.append((str(mid), qid, None, answer, self.account['ilink_user_id'], event_states))
        self.store.external_events(self.channel, events, states)

    def poll_once(self):
        self.accept(self.api.updates(self.store.state_get('weixin_cursor')))

    def run(self, stop):
        try:
            while not stop.is_set():
                if not self.store.lease(self.owner, channel=self.channel, ttl=self.lease_seconds):
                    logging.error('微信接收租约丢失，停止本消费者。')
                    return
                if self.stale:
                    self.deliver_one()
                    stop.wait(3)
                    continue
                try:
                    self.deliver_one()
                    if self.stale or stop.is_set():
                        continue
                    self.poll_once()
                    # 新上下文到达立即发送，不再等下一次长轮询。
                    self.deliver_one()
                    stop.wait(0.2)
                except WeixinError as exc:
                    self.stale = exc.code == -14
                    logging.warning('weixin: %s', exc)
                    stop.wait(5)
                except Exception:
                    # 未落盘的回答与游标一起重收；发送中断的记录只能标为结果未知。
                    self.store.recover_sending(self.channel)
                    logging.warning('微信轮询或存储异常，将保留游标重试；详情已隐藏。')
                    stop.wait(5)
        finally:
            self.close()
