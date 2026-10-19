"""只负责官方 SDK 长连接；父进程落盘成功后才允许 SDK 返回成功 ACK。"""
import json
import os
import sys


def exchange(payload, output, acknowledgement):
    output.write(json.dumps(payload, ensure_ascii=False) + '\n')
    output.flush()
    if acknowledgement.readline().strip() != 'ok':
        raise RuntimeError('事件未完成持久化，请平台重试。')


def main():
    protocol_output = sys.stdout
    sys.stdout = sys.stderr  # SDK 的日志不能混入 JSONL 协议，更不能输出到 MCP。
    import lark_oapi as lark

    def callback(data):
        exchange(json.loads(lark.JSON.marshal(data)), protocol_output, sys.stdin)

    handler = lark.EventDispatcherHandler.builder('', '').register_p2_im_message_receive_v1(callback).build()
    client = lark.ws.Client(os.environ['FEISHU_APP_ID'], os.environ['FEISHU_APP_SECRET'],
                            event_handler=handler, log_level=lark.LogLevel.ERROR)
    client.start()


if __name__ == '__main__':
    main()
