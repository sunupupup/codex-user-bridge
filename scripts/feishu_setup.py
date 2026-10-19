"""临时接收飞书私聊，仅显示 open_id，不显示消息正文或凭据。"""
import json
import os
import subprocess
import sys


def main():
    if not os.getenv('FEISHU_APP_ID') or not os.getenv('FEISHU_APP_SECRET'):
        raise SystemExit('请先配置 FEISHU_APP_ID、FEISHU_APP_SECRET。')
    process = subprocess.Popen([sys.executable, '-m', 'codex_user_bridge.feishu_receiver'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding='utf-8')
    print('请启用飞书长连接事件并给机器人发私聊；仅显示接收者 open_id。', flush=True)
    try:
        for line in process.stdout:
            event = json.loads(line).get('event', {})
            sender, message = event.get('sender', {}), event.get('message', {})
            if sender.get('sender_type') == 'user' and message.get('chat_type') == 'p2p':
                open_id = sender.get('sender_id', {}).get('open_id')
                if open_id:
                    print(f'FEISHU_OPEN_ID={open_id} （请确认这是你自己的私聊）', flush=True)
            process.stdin.write('ok\n')
            process.stdin.flush()
    except KeyboardInterrupt:
        pass
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=10)
        process.stdin.close()
        process.stdout.close()


if __name__ == '__main__':
    main()
