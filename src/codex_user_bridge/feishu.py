"""飞书自建应用：REST 发消息，官方 SDK 子进程接收长连接事件。"""
import json
import logging
import os
import subprocess
import sys
import threading
import time

from .channel_base import ChannelError, ExternalChannel, check_base, json_request, parse_reply, question_text


class FeishuApi:
    def __init__(self, app_id, app_secret, base='https://open.feishu.cn'):
        if not app_id or not app_secret:
            raise ValueError('请配置 FEISHU_APP_ID 和 FEISHU_APP_SECRET。')
        self.app_id, self._secret = app_id, app_secret
        self.base = check_base(base, {'https://open.feishu.cn'})
        self._token, self._expires = '', 0

    def validate(self):
        self.token()

    def token(self):
        if self._expires <= time.time():
            result = json_request(self.base + '/open-apis/auth/v3/tenant_access_token/internal',
                                  {'app_id': self.app_id, 'app_secret': self._secret})
            if result.get('code') != 0 or not result.get('tenant_access_token'):
                raise ChannelError('飞书凭据无效或应用不可用。')
            self._token = result['tenant_access_token']
            self._expires = time.time() + max(1, int(result.get('expire', 60)) - 60)
        return self._token

    def send(self, open_id, question):
        body = {'receive_id': open_id, 'msg_type': 'text',
                'content': json.dumps({'text': question_text(question)}, ensure_ascii=False),
                'uuid': question['question_id']}
        result = json_request(self.base + '/open-apis/im/v1/messages?receive_id_type=open_id', body,
                              {'Authorization': 'Bearer ' + self.token()})
        if result.get('code') != 0:
            raise ChannelError('飞书拒绝发送，请检查发消息权限、用户可用范围或限流。')
        message_id = result.get('data', {}).get('message_id')
        if not isinstance(message_id, str) or not message_id:
            raise ChannelError('飞书未返回消息编号，结果未知。', uncertain=True)
        return message_id


class FeishuChannel(ExternalChannel):
    channel = 'feishu'

    def __init__(self, store, api, open_id):
        if not open_id or not open_id.startswith('ou_'):
            raise ValueError('请配置接收者 FEISHU_OPEN_ID（ou_ 开头）。')
        self.open_id = open_id
        super().__init__(store, api, f'{api.app_id}:{open_id}')

    @classmethod
    def from_env(cls, store):
        return cls(store, FeishuApi(os.getenv('FEISHU_APP_ID'), os.getenv('FEISHU_APP_SECRET')),
                   os.getenv('FEISHU_OPEN_ID'))

    def send(self, question):
        return self.api.send(self.open_id, question)

    def accept(self, payload):
        header, event = payload.get('header', {}), payload.get('event', {})
        sender, message = event.get('sender', {}), event.get('message', {})
        if (header.get('app_id') != self.api.app_id or sender.get('sender_type') != 'user'
                or sender.get('sender_id', {}).get('open_id') != self.open_id
                or message.get('chat_type') != 'p2p' or message.get('message_type') != 'text'):
            return
        try:
            text = json.loads(message.get('content', '{}')).get('text', '')
        except (ValueError, AttributeError):
            return
        qid, answer = parse_reply(text)
        parent = message.get('parent_id')
        if not qid and parent and isinstance(text, str) and text.strip() and len(text) <= 4000:
            answer = text.strip()
        # 以 message_id 去重，而不是可能在重投中变化的 header.event_id。
        event_id = message.get('message_id')
        if event_id:
            self.store.external_events(self.channel, [(event_id, qid, parent, answer, self.open_id)])

    def run(self, stop):
        # SDK 自己拥有 asyncio 事件循环；隔离到子进程，停止时可确定关闭长连接。
        process = subprocess.Popen([sys.executable, '-m', 'codex_user_bridge.feishu_receiver'],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   text=True, encoding='utf-8')

        def receive():
            try:
                for line in process.stdout:
                    try:
                        self.accept(json.loads(line))
                        process.stdin.write('ok\n')
                    except Exception:
                        process.stdin.write('retry\n')
                    process.stdin.flush()
            except (OSError, ValueError):
                pass

        reader = threading.Thread(target=receive, daemon=True)
        reader.start()
        try:
            while not stop.is_set():
                if process.poll() is not None:
                    logging.error('飞书长连接进程退出，请检查应用事件配置、凭据与网络。')
                    return
                if not self.store.lease(self.owner, channel=self.channel):
                    return
                try:
                    self.deliver_one()
                except Exception:
                    logging.error('飞书处理失败，请检查数据库与配置；不会盲目重发。')
                    self.store.recover_sending(self.channel)
                stop.wait(1)
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)
            reader.join(timeout=5)
            process.stdin.close()
            process.stdout.close()
            self.close()
