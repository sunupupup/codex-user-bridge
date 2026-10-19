"""读取 Bot 身份和待接收消息中的私聊 ID；不输出 Token、不发送消息。"""
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from codex_user_bridge.telegram import TelegramApi, TelegramError


def main():
    try:
        api = TelegramApi(os.environ.get('TELEGRAM_BOT_TOKEN', ''))
        api.validate()
        me = api.call('getMe')
        print(f"Bot: @{me.get('username', '')}，id={me['id']}")
        seen = set()
        # 不推进 offset，不输出用户文本。运行本助手前应停止其他 getUpdates 消费者。
        for update in api.updates(0, timeout=0):
            message = update.get('message', {})
            chat, sender = message.get('chat', {}), message.get('from', {})
            if chat.get('type') == 'private' and not sender.get('is_bot'):
                seen.add((chat.get('id'), sender.get('id')))
        for chat_id, user_id in sorted(seen):
            print(f'TELEGRAM_CHAT_ID={chat_id}  TELEGRAM_USER_ID={user_id}')
        if not seen:
            print('未发现待接收私聊：请先在 Telegram 向此 Bot 发送 /start，再运行本助手。')
    except (ValueError, TelegramError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == '__main__':
    main()
