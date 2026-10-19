"""用户手动运行：扫描个人微信二维码，凭据只保存到本机。"""
import argparse
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from codex_user_bridge.channel_base import ChannelError
from codex_user_bridge.weixin import credentials_path
from codex_user_bridge.weixin_login import login


def show_qr(content):
    import qrcode
    qr = qrcode.QRCode(border=2)
    qr.add_data(content)
    print('请使用手机微信扫码，并按手机提示完成确认。请勿分享此二维码。')
    qr.print_ascii(invert=True)
    print('若终端二维码不便扫描，可在本机打开以下登录链接：\n' + content)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--credentials', type=Path,
        default=credentials_path() if os.environ.get('WEIXIN_CREDENTIALS') else Path(__file__).resolve().parents[1] / '.local/weixin-account.json')
    args = parser.parse_args()
    try:
        login(args.credentials, show_qr, input)
    except (ValueError, ChannelError) as exc:
        parser.exit(2, str(exc) + '\n')
    except KeyboardInterrupt:
        parser.exit(130, '已取消，未保存未完成的登录。\n')
    print('微信登录完成，凭据已保存至 ' + str(args.credentials.resolve()))
    print('启动微信服务后，请先在手机给机器人发送“你好”，建立会话上下文。')


if __name__ == '__main__':
    main()
